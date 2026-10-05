#!/usr/bin/env python3
"""voice-rec web UI server.

Runs main.py (unchanged console recorder) as a subprocess and exposes a browser
UI: live transcript, screenshots with statuses, capture-area selection,
Beautify (annotated full transcript via Claude Code) and a chat about the
transcript that works while recording continues.

    python server.py            # http://127.0.0.1:8765 (opens the browser)
    python server.py --port 9000 --no-browser
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import sys
import time
import uuid
import webbrowser
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import beautify
import claude_cli
import sessions as S

ROOT = S.ROOT
WEB_DIR = ROOT / "web"
HELPER = ROOT / "screenshot_helper"
CAPTURE_CONFIG = S.WEBUI_DIR / "capture.json"
CAPTURE_PREVIEW = S.WEBUI_DIR / "capture_preview.png"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
NOISY_LOG = ("● speech", "[transcribing", "[embedding]")
STREAM_LIMIT = 64 * 1024 * 1024   # stream-json lines can carry whole documents


# ── WebSocket hub ────────────────────────────────────────────────────────────

class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def send(self, msg: dict):
        dead = []
        data = json.dumps(msg, ensure_ascii=False, default=str)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def emit(self, msg: dict):
        asyncio.get_running_loop().create_task(self.send(msg))


hub = Hub()


async def run_cmd(*cmd, timeout=120) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", "timeout"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


# ── Recorder (main.py subprocess) ────────────────────────────────────────────

class Recorder:
    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.state = "idle"     # idle | loading | listening | stopping | finalizing | error
        self.detail = ""
        self.project = ""
        self.sid = ""
        self.transcript_path = ""
        self.started_at = 0.0
        self.listening_at = 0.0
        self.lang = ""
        self.diarization = False
        self.error = ""
        self.entries: list[dict] = []
        self.shots: dict[str, dict] = {}
        self.log: deque[str] = deque(maxlen=400)
        self.activity = {"kind": "idle"}
        self.options: dict = {}
        self.deleted: set[str] = set()
        self.session_start: datetime | None = None
        # speech = seconds of the phrase being recorded (None = silence);
        # current = chunk Whisper is working on; pending = chunks waiting in the queue
        self.pipeline = {"speech": None, "current": None, "pending": [], "pause": None}

    @property
    def active(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def snapshot(self) -> dict:
        return {
            "state": self.state, "detail": self.detail, "project": self.project, "sid": self.sid,
            "started_at": self.started_at, "listening_at": self.listening_at, "lang": self.lang,
            "diarization": self.diarization, "error": self.error, "active": self.active,
            "activity": self.activity, "options": self.options, "pipeline": self.pipeline,
            "minutes": self.options.get("minutes"),
        }

    def live_session(self) -> dict:
        return {
            "project": self.project, "sid": self.sid, "live": True,
            "transcript_path": self.transcript_path, "entries": self.entries,
            "deleted": sorted(self.deleted),
            "screenshots": list(self.shots.values()), "diarized": False,
            "manifest": S.public_manifest(self.project, self.sid, S.load_manifest(self.project, self.sid))
            if self.sid else {"versions": []},
        }

    def set_state(self, state: str, detail: str = ""):
        self.state, self.detail = state, detail
        hub.emit({"type": "recorder", **self.snapshot()})

    async def start(self, opts: dict):
        if self.active:
            raise HTTPException(409, "A recording is already running")
        project = S.safe_name(opts.get("project", ""))
        settings = S.load_settings()
        python = settings.get("recorder_python") or sys.executable
        self.__init__()
        self.project = project
        self.options = opts
        self.started_at = time.time()
        pdir = S.project_dir(project)
        (pdir / "sessions").mkdir(parents=True, exist_ok=True)
        events_path = pdir / "sessions" / f"live-{datetime.now():%Y%m%d_%H%M%S}.events.jsonl"
        events_path.write_text("")
        if not CAPTURE_CONFIG.exists():
            S._write_json(CAPTURE_CONFIG, {"mode": "full"})

        cmd = [python, "-u", str(ROOT / "main.py"), project, str(float(opts.get("minutes") or 180)),
               "--events", str(events_path), "--control-stdin", "--capture-config", str(CAPTURE_CONFIG),
               "--lang", str(opts.get("lang") or "auto"), "--pause", str(float(opts.get("pause") or 1.5)),
               "--vocab-file", str(S.notes_path(project).resolve())]
        speakers = str(opts.get("speakers") or "off")
        if speakers != "off":
            cmd += ["--speakers", speakers]
            self.diarization = True
        if opts.get("save_audio"):
            cmd.append("--save-audio")
        self.pipeline["pause"] = float(opts.get("pause") or 1.5)

        self.log.append("$ " + " ".join(cmd[1:]))
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(ROOT), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"}, limit=STREAM_LIMIT)
        S.save_settings({"last_project": project, **{k: opts[k] for k in
                         ("lang", "speakers", "minutes", "pause", "save_audio") if k in opts}})
        self.set_state("loading", "Starting recognizer")
        loop = asyncio.get_running_loop()
        loop.create_task(self._read_output())
        loop.create_task(self._tail_events(events_path))
        loop.create_task(self._wait_exit(events_path))

    async def stop(self):
        if not self.active:
            return
        self.proc.send_signal(signal.SIGINT)      # same as Ctrl+C in the console
        if self.state in ("loading", "listening"):
            self.set_state("stopping", "Stopping")

    async def snap(self):
        if not self.active or self.state != "listening":
            raise HTTPException(409, "Not recording")
        self.proc.stdin.write(b"SNAP\n")
        await self.proc.stdin.drain()

    async def _read_output(self):
        buf = b""
        while True:
            chunk = await self.proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for p in parts:
                line = ANSI_RE.sub("", p.decode("utf-8", errors="replace")).strip()
                if not line or line.startswith(NOISY_LOG):
                    continue
                self.log.append(line)
                hub.emit({"type": "log", "line": line})

    async def _tail_events(self, path: Path):
        pending = ""
        idle_after_exit = 0
        with open(path, "r", encoding="utf-8") as f:
            while True:
                chunk = f.readline()
                if not chunk:
                    if not self.active:
                        idle_after_exit += 1
                        if idle_after_exit > 5:
                            break
                    await asyncio.sleep(0.04)
                    continue
                pending += chunk
                if not pending.endswith("\n"):
                    continue
                line, pending = pending, ""
                try:
                    await self._handle(json.loads(line))
                except Exception as e:  # never let one bad event kill the tail
                    print(f"[server] event error: {e}", file=sys.stderr)

    def _wall(self, t: float) -> str:
        if not self.session_start:
            return ""
        return (self.session_start + timedelta(seconds=t)).strftime("%H:%M:%S")

    def _pipeline(self, t: str, ev: dict):
        pl = self.pipeline
        if t == "speech_start":
            pl["speech"] = 0.0
        elif t == "speech":
            pl["speech"] = ev.get("duration", 0.0)
        elif t == "speech_end":
            pl["speech"] = None
            if ev.get("queued"):
                pl["pending"].append({"t": ev["t"], "time": self._wall(ev["t"]), "duration": ev.get("duration", 0)})
        elif t == "transcribing":
            pl["pending"] = [c for c in pl["pending"] if c["t"] != ev["t"]]
            pl["current"] = {"t": ev["t"], "time": self._wall(ev["t"]), "duration": ev.get("duration", 0),
                             "done": 0.0, "text": "", "started": ev.get("ts", time.time())}
        elif t == "transcribe_progress":
            cur = pl["current"]
            if cur and cur["t"] == ev["t"]:
                cur["done"] = ev.get("done", cur["done"])
                if ev.get("text"):
                    cur["text"] = (cur["text"] + " " + ev["text"]).strip()
        elif t == "chunk_done":
            if pl["current"] and pl["current"]["t"] == ev["t"]:
                pl["current"] = None
            pl["pending"] = [c for c in pl["pending"] if c["t"] != ev["t"]]
        elif t == "pause":
            pl["pause"] = ev.get("seconds")
        hub.emit({"type": "pipeline", **pl})

    async def set_pause(self, seconds: float):
        if not self.active:
            return
        self.proc.stdin.write(f"PAUSE {seconds}\n".encode())
        await self.proc.stdin.drain()

    def forget_shot(self, path: str):
        self.deleted.add(path)
        self.shots.pop(path, None)
        for e in self.entries:
            if e.get("kind") == "screenshot" and e.get("path") == path:
                e["deleted"] = True

    def _shot(self, path: str) -> dict:
        if path in self.deleted:
            return {"path": path, "deleted": True}
        if path not in self.shots:
            self.shots[path] = {**S.screenshot_info(path), "logged": False, "ocr": False, "captured": True}
        return self.shots[path]

    async def _handle(self, ev: dict):
        t = ev.get("type")
        if t == "level":
            hub.emit({"type": "level", "rms": ev.get("rms", 0)})
        elif t == "status":
            st = ev.get("state")
            if st == "listening":
                self.listening_at = ev.get("ts", time.time())
            self.set_state(st, ev.get("detail", ""))
        elif t == "session":
            self.sid = ev["timestamp"]
            try:
                self.session_start = datetime.fromisoformat(ev["start_time"])
            except (KeyError, ValueError):
                self.session_start = None
            self.transcript_path = ev["transcript_path"]
            self.diarization = ev.get("diarization", False)
            hub.emit({"type": "recorder", **self.snapshot()})
        elif t in ("speech_start", "speech", "speech_end", "transcribing", "transcribe_progress",
                   "chunk_done", "pause"):
            self._pipeline(t, ev)
        elif t == "segment_empty":
            pass
        elif t == "segment":
            entry = {"time": ev["time"], "kind": "speech", "speaker": None, "text": ev["text"]}
            self._add_entry(entry)
        elif t == "injection":
            if ev.get("kind") == "screenshot":
                shot = self._shot(ev["path"])
                shot["logged"] = True
                self._add_entry({"time": ev["time"], "kind": "screenshot", "speaker": None,
                                 "text": "", "path": ev["path"]})
                self._emit_shot(shot)
            else:
                self._add_entry({"time": ev["time"], "kind": "selection", "speaker": None,
                                 "text": ev.get("text", "")})
        elif t == "screenshot":
            shot = self._shot(ev["path"])
            self._emit_shot(shot, new=True)
        elif t in ("ocr", "ocr_error"):
            shot = self._shot(ev["path"])
            shot["ocr"] = t == "ocr"
            shot["ocr_chars"] = ev.get("chars", 0)
            shot["ocr_error"] = ev.get("message") if t == "ocr_error" else None
            self._emit_shot(shot)
        elif t == "lang":
            self.lang = ev.get("lang", "")
            hub.emit({"type": "recorder", **self.snapshot()})
        elif t == "embedding":
            hub.emit({"type": "embedding", "count": ev.get("count", 0)})
        elif t == "diarization":
            label = {"clustering": f"Clustering {ev.get('windows', 0)} voice samples",
                     "labeling": f"Labelling {ev.get('speakers', 0)} speakers",
                     "done": "Speakers assigned"}.get(ev.get("state"), "Diarization")
            self.set_state("finalizing", label)
        elif t == "error":
            hub.emit({"type": "toast", "level": "error", "text": f"{ev.get('where')}: {ev.get('message')}"})

    def _emit_shot(self, shot: dict, new: bool = False):
        if not shot.get("deleted"):
            hub.emit({"type": "shot", "project": self.project, "sid": self.sid, "shot": shot, "new": new})

    def _add_entry(self, entry: dict):
        if entry.get("kind") == "screenshot" and entry.get("path") in self.deleted:
            entry["deleted"] = True
        self.entries.append(entry)
        hub.emit({"type": "entry", "project": self.project, "sid": self.sid,
                  "idx": len(self.entries) - 1, "entry": entry})

    async def _wait_exit(self, events_path: Path):
        rc = await self.proc.wait()
        await asyncio.sleep(0.5)  # let the tail drain
        project, sid = self.project, self.sid
        if sid:
            sdir = S.session_dir(project, sid)
            sdir.mkdir(parents=True, exist_ok=True)
            try:
                events_path.replace(sdir / "events.jsonl")
            except OSError:
                pass
        if rc not in (0, -signal.SIGINT) and self.state not in ("finalizing",):
            tail = "\n".join(list(self.log)[-12:])
            self.error = f"Recognizer exited with code {rc}"
            self.set_state("error", tail)
        else:
            self.set_state("idle", "Session finished")
        if sid:
            hub.emit({"type": "session_changed", "project": project, "sid": sid, "finished": True})
            if S.load_settings().get("auto_final_beautify") and S.transcript_path(project, sid).exists():
                try:
                    await jobs.start(project, sid, "final")
                except HTTPException:
                    pass


recorder = Recorder()


# ── Beautify jobs ────────────────────────────────────────────────────────────

class BeautifyJobs:
    def __init__(self):
        self.jobs: dict[tuple[str, str], dict] = {}
        self.procs: dict[tuple[str, str], asyncio.subprocess.Process] = {}

    def public(self, job: dict) -> dict:
        return {k: v for k, v in job.items() if k != "spec"}

    def broadcast(self, job: dict):
        hub.emit({"type": "beautify", **self.public(job)})

    def running(self, project, sid) -> bool:
        j = self.jobs.get((project, sid))
        return bool(j and j["status"] == "running")

    async def start(self, project: str, sid: str, mode: str) -> dict:
        key = (project, sid)
        if self.running(project, sid):
            raise HTTPException(409, "Beautify is already running for this session")
        live = recorder.active and recorder.project == project and recorder.sid == sid
        if mode == "final" and live:
            mode = "full"
        spec = await asyncio.to_thread(beautify.prepare, project, sid, mode, live)
        job = {"project": project, "sid": sid, "v": spec["v"], "mode": spec["mode"], "status": "running",
               "started": time.time(), "steps": [], "chars": 0, "summary": "", "error": "",
               "lines": spec["lines"], "t_last": spec["t_last"], "shots": spec["shot_count"], "spec": spec}
        self.jobs[key] = job
        self.step(job, {"update": "Updating the previous version",
                        "full": "Rebuilding the full document",
                        "final": "Building the final version"}[spec["mode"]])
        asyncio.get_running_loop().create_task(self._run(job))
        return self.public(job)

    def step(self, job: dict, text: str, replace_last_tool: str | None = None):
        t = round(time.time() - job["started"], 1)
        if replace_last_tool and job["steps"] and job["steps"][-1].get("tool") == replace_last_tool \
                and job["steps"][-1].get("generic"):
            job["steps"][-1].update(text=text, generic=False)
        else:
            job["steps"].append({"t": t, "text": text, "tool": replace_last_tool})
        self.broadcast(job)

    async def cancel(self, project: str, sid: str):
        proc = self.procs.get((project, sid))
        job = self.jobs.get((project, sid))
        if job and job["status"] == "running":
            job["cancelled"] = True
        if proc and proc.returncode is None:
            proc.terminate()

    async def _run(self, job: dict):
        spec = job["spec"]
        key = (job["project"], job["sid"])
        result: dict = {}
        stderr_tail = ""
        try:
            if spec["settings"]["beautify_engine"] == "api":
                self.step(job, "Generating with API model (single pass)")
                await asyncio.to_thread(beautify.run_api, spec)
                ok = True
            else:
                proc = await asyncio.create_subprocess_exec(
                    *beautify.claude_command(spec), cwd=str(spec["cwd"]), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT)
                self.procs[key] = proc
                proc.stdin.write(spec["prompt"].encode())
                await proc.stdin.drain()
                proc.stdin.close()
                last_push = 0.0
                tool_blocks: dict[int, str] = {}
                async for raw in proc.stdout:
                    ev = claude_cli.parse_line(raw)
                    if not ev:
                        continue
                    et = ev.get("type")
                    if et == "system" and ev.get("subtype") == "init":
                        mcp = [m["name"] for m in ev.get("mcp_servers", []) if m.get("status") == "connected"]
                        has_ctx = "claude-context" in mcp
                        self.step(job, f"Claude ({ev.get('model', '')}) started"
                                       + (" · project knowledge connected" if has_ctx else ""))
                    elif et == "stream_event":
                        inner = ev.get("event", {})
                        if inner.get("type") == "content_block_start":
                            cb = inner.get("content_block", {})
                            if cb.get("type") == "tool_use":
                                tool_blocks[inner.get("index", 0)] = cb.get("name", "")
                                job["steps"].append({"t": round(time.time() - job["started"], 1),
                                                     "text": claude_cli.generic_label(cb.get("name", "")),
                                                     "tool": cb.get("name"), "generic": True})
                                self.broadcast(job)
                        elif inner.get("type") == "content_block_delta":
                            d = inner.get("delta", {})
                            job["chars"] += len(d.get("text") or d.get("partial_json") or "")
                            if time.time() - last_push > 0.5:
                                last_push = time.time()
                                hub.emit({"type": "beautify_progress", "project": job["project"],
                                          "sid": job["sid"], "chars": job["chars"]})
                    elif et == "assistant":
                        for name, inp in claude_cli.tool_uses(ev):
                            self.step(job, claude_cli.describe_tool(name, inp, spec["labels"]),
                                      replace_last_tool=name)
                    elif et == "result":
                        result = ev
                err = await proc.stderr.read()
                stderr_tail = err.decode(errors="replace")[-600:]
                await proc.wait()
                ok = proc.returncode == 0 and not result.get("is_error")
        except Exception as e:
            ok = False
            stderr_tail = str(e)
        self.procs.pop(key, None)

        if job.get("cancelled"):
            status, error = "cancelled", "Cancelled"
        elif ok:
            status, error = "done", ""
        else:
            status = "failed"
            error = (result.get("result") or stderr_tail or "Beautify failed").strip()[-600:]
        rec = await asyncio.to_thread(
            beautify.finish, job["project"], job["sid"], job["v"], status,
            duration=round(time.time() - job["started"]), summary=(result.get("result") or "")[:500],
            cost=result.get("total_cost_usd"), error=error)
        job.update(status=rec["status"], error=rec.get("error", ""), summary=rec.get("summary", ""),
                   finished=time.time(), record=rec)
        self.step(job, {"done": "Finished", "failed": "Failed", "cancelled": "Cancelled"}[rec["status"]])
        hub.emit({"type": "session_changed", "project": job["project"], "sid": job["sid"]})
        if recorder.active and recorder.project == job["project"] and recorder.sid == job["sid"]:
            for shot in recorder.shots.values():
                if shot["name"] in rec.get("screenshots", []) and rec["status"] == "done":
                    shot["beautified"] = rec["v"]
                    shot["in_doc"] = shot["name"] in rec.get("referenced", [])
                    hub.emit({"type": "shot", "project": job["project"], "sid": job["sid"], "shot": shot})


jobs = BeautifyJobs()


# ── Chat about the transcript ────────────────────────────────────────────────

CHAT_SYSTEM = """You are the assistant inside a live transcription app. The user asks about the transcript \
of a meeting or walkthrough, which may still be recording. Answer concisely in the language of the question, \
using Markdown. Cite moments with their [HH:MM:SS] timestamps. The transcript is raw speech-to-text, so expect \
recognition errors and interpret sensibly. Lines like [screenshot: path] point to screen captures you can view \
with the Read tool when relevant. If a project knowledge base (claude-context MCP) is available, use it when the \
question needs background. Never modify files."""


class ChatManager:
    def __init__(self):
        self.busy: set[tuple[str, str]] = set()
        self.procs: dict[tuple[str, str], asyncio.subprocess.Process] = {}

    def path(self, project, sid) -> Path:
        return S.session_dir(project, sid) / "chat.json"

    def load(self, project, sid) -> dict:
        return S._read_json(self.path(project, sid),
                            {"claude_session": None, "sent_len": 0, "sent_hash": "", "messages": []})

    def save(self, project, sid, data):
        S._write_json(self.path(project, sid), data)

    async def ask(self, project: str, sid: str, text: str) -> dict:
        key = (project, sid)
        if key in self.busy:
            raise HTTPException(409, "Still answering the previous question")
        data = self.load(project, sid)
        user_msg = {"id": uuid.uuid4().hex[:10], "role": "user", "text": text, "ts": time.time()}
        bot_msg = {"id": uuid.uuid4().hex[:10], "role": "assistant", "text": "", "ts": time.time(),
                   "tools": [], "status": "thinking"}
        data["messages"] += [user_msg, bot_msg]
        self.save(project, sid, data)
        self.busy.add(key)
        hub.emit({"type": "chat_message", "project": project, "sid": sid, "message": user_msg})
        hub.emit({"type": "chat_message", "project": project, "sid": sid, "message": bot_msg})
        asyncio.get_running_loop().create_task(self._run(project, sid, text, bot_msg["id"]))
        return {"user": user_msg, "assistant": bot_msg}

    def _prompt(self, project, sid, data, question) -> str:
        path = S.transcript_path(project, sid)
        transcript = S.read_transcript(project, sid)
        live = recorder.active and recorder.project == project and recorder.sid == sid
        status = "still recording" if live else "recording finished"
        prefix_ok = (data["claude_session"] and data["sent_len"] <= len(transcript) and
                     hashlib.md5(transcript[:data["sent_len"]].encode()).hexdigest() == data["sent_hash"])
        notes = S.load_notes(project).strip()
        notes_hash = hashlib.md5(notes.encode()).hexdigest()
        notes_note = ""
        if notes and data.get("notes_hash") != notes_hash:
            notes_note = ("The user's notes and corrections for this project (authoritative; apply corrections "
                          f"to the transcript when answering):\n<notes>\n{notes}\n</notes>\n\n")
        data["notes_hash"] = notes_hash
        if prefix_ok:
            delta = transcript[data["sent_len"]:]
            body = (f"New transcript lines since my last message ({status}):\n<transcript_update>\n{delta}"
                    f"</transcript_update>\n\n" if delta.strip() else f"(No new transcript lines; {status}.)\n\n")
        else:
            # First question, or the transcript was rewritten (e.g. speakers assigned): send it in full.
            intro = "Updated full transcript (it was re-processed)" if data["claude_session"] else "Transcript"
            body = (f"{intro} — file: {path} ({status}).\n"
                    + f"<transcript>\n{transcript}</transcript>\n\n")
        data["sent_len"] = len(transcript)
        data["sent_hash"] = hashlib.md5(transcript.encode()).hexdigest()
        return notes_note + body + f"Question: {question}"

    async def _run(self, project, sid, question, msg_id):
        key = (project, sid)
        data = self.load(project, sid)
        msg = next(m for m in data["messages"] if m["id"] == msg_id)
        settings = S.load_settings()
        psettings = S.load_project_settings(project)
        ctx = Path(psettings["context_dir"]).expanduser() if psettings["context_dir"] else ROOT
        cwd = ctx if ctx.is_dir() else ROOT

        def push(**kw):
            hub.emit({"type": "chat_update", "project": project, "sid": sid, "id": msg_id, **kw})

        try:
            prompt = self._prompt(project, sid, data, question)
            cmd = claude_cli.build_command(
                settings["chat_model"], ["Read", "Glob", "Grep", "mcp__claude-context"],
                [S.project_dir(project).resolve()], resume=data["claude_session"], append_system=CHAT_SYSTEM)
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(cwd), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT)
            self.procs[key] = proc
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            result = {}
            async for raw in proc.stdout:
                ev = claude_cli.parse_line(raw)
                if not ev:
                    continue
                delta = claude_cli.text_delta(ev)
                if delta:
                    if msg["text"] and msg.get("_after_tool"):
                        delta = "\n\n" + delta
                        msg["_after_tool"] = False
                    msg["text"] += delta
                    msg["status"] = "streaming"
                    push(delta=delta, status="streaming")
                for name, inp in claude_cli.tool_uses(ev):
                    label = claude_cli.describe_tool(name, inp)
                    msg["tools"].append(label)
                    msg["_after_tool"] = True
                    push(tool=label)
                if ev.get("type") == "result":
                    result = ev
            err = (await proc.stderr.read()).decode(errors="replace")
            await proc.wait()
            if result.get("session_id"):
                data["claude_session"] = result["session_id"]
            if proc.returncode != 0 or result.get("is_error"):
                raise RuntimeError((result.get("result") or err or "Chat failed").strip()[-400:])
            if not msg["text"] and result.get("result"):
                msg["text"] = result["result"]
            msg["status"] = "done"
        except Exception as e:
            msg["status"] = "error"
            msg["error"] = str(e)
        finally:
            msg.pop("_after_tool", None)
            self.procs.pop(key, None)
            self.busy.discard(key)
            self.save(project, sid, data)
            push(status=msg["status"], text=msg["text"], error=msg.get("error"), done=True)

    def clear(self, project, sid):
        if self.path(project, sid).exists():
            self.path(project, sid).unlink()


chat = ChatManager()


# ── App ──────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    S.WEBUI_DIR.mkdir(exist_ok=True)
    yield
    if recorder.active:
        print("Stopping the running recording (finalizing, please wait)...")
        recorder.proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(recorder.proc.wait(), 600)
        except asyncio.TimeoutError:
            recorder.proc.kill()
    for p in list(jobs.procs.values()) + list(chat.procs.values()):
        if p.returncode is None:
            p.terminate()


app = FastAPI(lifespan=lifespan)
S.PROJECTS_DIR.mkdir(exist_ok=True)
app.mount("/files", StaticFiles(directory=str(S.PROJECTS_DIR)), name="files")
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    await ws.send_text(json.dumps({
        "type": "hello", "recorder": recorder.snapshot(), "log": list(recorder.log)[-150:],
        "jobs": [jobs.public(j) for j in jobs.jobs.values()],
        "chat_busy": [list(k) for k in chat.busy],
    }, default=str))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)


def _err(e: Exception):
    raise HTTPException(400, str(e))


@app.get("/api/status")
async def status():
    return {"claude": bool(claude_cli.claude_binary()), "helper": HELPER.exists(),
            "settings": S.load_settings(), "recorder": recorder.snapshot()}


@app.get("/api/check-recorder")
async def check_recorder():
    python = S.load_settings().get("recorder_python") or sys.executable
    rc, out, err = await run_cmd(python, "-c", "import faster_whisper, sounddevice, easyocr, torch; print('ok')")
    return {"ok": rc == 0, "python": python, "error": (err or out).strip()[-300:]}


@app.get("/api/settings")
async def get_settings():
    return S.load_settings()


@app.post("/api/settings")
async def set_settings(data: dict = Body(...)):
    return S.save_settings(data)


@app.get("/api/projects")
async def projects():
    return S.list_projects()


@app.post("/api/projects")
async def create_project(data: dict = Body(...)):
    try:
        name = S.safe_name(data.get("name", ""))
    except ValueError as e:
        _err(e)
    for sub in ("transcripts", "screenshots", "docs"):
        (S.PROJECTS_DIR / name / sub).mkdir(parents=True, exist_ok=True)
    return {"name": name}


@app.get("/api/projects/{project}/settings")
async def project_settings(project: str):
    return S.load_project_settings(project)


@app.post("/api/projects/{project}/settings")
async def save_project_settings(project: str, data: dict = Body(...)):
    return S.save_project_settings(project, data)


@app.get("/api/projects/{project}/sessions")
async def project_sessions(project: str):
    out = await asyncio.to_thread(S.list_sessions, project)
    if recorder.active and recorder.project == project and recorder.sid:
        for s in out:
            s["live"] = s["sid"] == recorder.sid
    return out


@app.get("/api/projects/{project}/sessions/{sid}")
async def session(project: str, sid: str):
    if recorder.active and recorder.project == project and recorder.sid == sid:
        return recorder.live_session()
    try:
        data = await asyncio.to_thread(S.load_session, project, sid)
    except ValueError as e:
        _err(e)
    return data


@app.get("/api/projects/{project}/sessions/{sid}/chat")
async def chat_history(project: str, sid: str):
    data = chat.load(project, sid)
    return {"messages": data["messages"], "busy": (project, sid) in chat.busy}


@app.post("/api/projects/{project}/sessions/{sid}/chat")
async def chat_ask(project: str, sid: str, data: dict = Body(...)):
    text = (data.get("text") or "").strip()
    if not text:
        _err(ValueError("Empty question"))
    if not claude_cli.claude_binary():
        _err(RuntimeError("Claude Code CLI is not installed"))
    return await chat.ask(project, sid, text)


@app.delete("/api/projects/{project}/sessions/{sid}/chat")
async def chat_clear(project: str, sid: str):
    chat.clear(project, sid)
    return {"ok": True}


@app.get("/api/projects/{project}/notes")
async def notes_get(project: str):
    return {"text": S.load_notes(project), "path": str(S.notes_path(project))}


@app.put("/api/projects/{project}/notes")
async def notes_put(project: str, data: dict = Body(...)):
    S.save_notes(project, data.get("text", ""))
    hub.emit({"type": "notes", "project": project, "text": data.get("text", ""), "client": data.get("client")})
    return {"ok": True}


@app.post("/api/projects/{project}/notes/correction")
async def notes_correction(project: str, data: dict = Body(...)):
    original, fixed = (data.get("original") or "").strip(), (data.get("fixed") or "").strip()
    if not original or not fixed or original == fixed:
        _err(ValueError("Nothing to correct"))
    text = S.add_correction(project, original, fixed, data.get("comment") or "")
    hub.emit({"type": "notes", "project": project, "text": text})
    return {"text": text}


@app.delete("/api/projects/{project}/sessions/{sid}/screenshots/{name}")
async def screenshot_delete(project: str, sid: str, name: str):
    path = S.project_dir(project) / "screenshots" / name
    try:
        S.delete_screenshot(project, str(path))
    except ValueError as e:
        _err(e)
    if recorder.project == project and recorder.sid == sid:
        recorder.forget_shot(str(path.resolve()))
        recorder.forget_shot(str(path))
    hub.emit({"type": "shot_deleted", "project": project, "sid": sid, "path": str(path.resolve()), "name": name})
    return {"ok": True}


@app.post("/api/projects/{project}/sessions/{sid}/beautify")
async def beautify_start(project: str, sid: str, data: dict = Body(default={})):
    mode = data.get("mode", "update")
    if mode not in ("update", "full", "final"):
        _err(ValueError("Bad mode"))
    try:
        return await jobs.start(project, sid, mode)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        _err(e)


@app.delete("/api/projects/{project}/sessions/{sid}/beautify")
async def beautify_cancel(project: str, sid: str):
    await jobs.cancel(project, sid)
    return {"ok": True}


# Recording

@app.post("/api/record/start")
async def record_start(data: dict = Body(...)):
    try:
        await recorder.start(data)
    except ValueError as e:
        _err(e)
    return recorder.snapshot()


@app.post("/api/record/stop")
async def record_stop():
    await recorder.stop()
    return recorder.snapshot()


@app.post("/api/record/pause")
async def record_pause(data: dict = Body(...)):
    try:
        seconds = round(max(0.2, min(10.0, float(data.get("seconds")))), 2)
    except (TypeError, ValueError):
        _err(ValueError("Bad pause value"))
    S.save_settings({"pause": seconds})
    await recorder.set_pause(seconds)
    return {"pause": seconds}


@app.post("/api/record/snap")
async def record_snap():
    await recorder.snap()
    return {"ok": True}


# Capture area

def _capture_info() -> dict:
    cfg = S._read_json(CAPTURE_CONFIG, {"mode": "full"})
    return {"config": cfg, "preview": f"/api/capture/preview?t={CAPTURE_PREVIEW.stat().st_mtime}"
            if CAPTURE_PREVIEW.exists() else None}


async def _refresh_preview():
    if HELPER.exists():
        await run_cmd(str(HELPER), "--capture", str(CAPTURE_CONFIG), str(CAPTURE_PREVIEW), timeout=20)


@app.get("/api/capture")
async def capture_get():
    return _capture_info()


@app.post("/api/capture")
async def capture_set(cfg: dict = Body(...)):
    if cfg.get("mode") not in ("full", "region", "window"):
        _err(ValueError("Bad capture mode"))
    S._write_json(CAPTURE_CONFIG, cfg)
    await _refresh_preview()
    return _capture_info()


@app.post("/api/capture/preview")
async def capture_preview_refresh():
    if not CAPTURE_CONFIG.exists():
        S._write_json(CAPTURE_CONFIG, {"mode": "full"})
    await _refresh_preview()
    return _capture_info()


@app.get("/api/capture/preview")
async def capture_preview():
    if not CAPTURE_PREVIEW.exists():
        raise HTTPException(404)
    return FileResponse(CAPTURE_PREVIEW, headers={"Cache-Control": "no-cache"})


@app.post("/api/capture/select")
async def capture_select(data: dict = Body(...)):
    """Show the native overlay: kind = 'region' (drag) or 'window' (click)."""
    flag = "--select-region" if data.get("kind") == "region" else "--pick-window"
    rc, out, err = await run_cmd(str(HELPER), flag, timeout=100)
    line = next((l for l in out.splitlines() if l.startswith(("REGION:", "WINDOW:"))), None)
    if not line:
        return JSONResponse({"cancelled": True, **_capture_info()})
    kind, payload = line.split(":", 1)
    obj = json.loads(payload)
    cfg = {"mode": "region", "rect": obj["rect"]} if kind == "REGION" else {"mode": "window", **obj}
    S._write_json(CAPTURE_CONFIG, cfg)
    await _refresh_preview()
    return _capture_info()


@app.get("/api/capture/windows")
async def capture_windows():
    rc, out, err = await run_cmd(str(HELPER), "--list-windows", timeout=20)
    try:
        wins = json.loads(out)
    except json.JSONDecodeError:
        return []
    seen, result = set(), []
    for w in wins:
        k = (w["owner"], w["title"])
        if k in seen or not (w["title"] or w["owner"]):
            continue
        seen.add(k)
        result.append(w)
    return result[:60]


def main():
    ap = argparse.ArgumentParser(description="voice-rec web UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    import uvicorn
    url = f"http://{args.host}:{args.port}"
    print(f"voice-rec web UI: {url}")
    if not args.no_browser:
        import threading
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
