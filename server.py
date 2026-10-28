#!/usr/bin/env python3
"""ushi web UI server.

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
import subprocess
import sys
import threading
import time
import uuid
import zipfile
import webbrowser
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import batch
import beautify
import claude_cli
import downloads
import llm_api
import external
import image_notes
import native_helpers
import select_frames as SF
import sessions as S

ENGINES = ("whisper", "gigaam", "parakeet")   # main.py --engine (see asr_engines.py)

ROOT = S.ROOT
WEB_DIR = ROOT / "web"
CAPTURE_CONFIG = S.WEBUI_DIR / "capture.json"
# main.py --source kinds; "video" / "video-rt" are files too (background / --realtime with a player)
SOURCES = ("mic", "app+mic", "app", "file", "video", "video-rt")
FILE_SOURCES = ("file", "video", "video-rt")
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
                                                stderr=asyncio.subprocess.PIPE, env=native_helpers.child_env())
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", "timeout"
    return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


async def run_helper(*args, timeout=120) -> tuple[int, str, str]:
    """Run the screenshot helper (see native_helpers); rc -1 when there is none."""
    cmd = native_helpers.screenshot_cmd()
    if not cmd:
        return -1, "", f"screenshot_helper is missing ({native_helpers.screenshot_build_hint()})"
    return await run_cmd(*cmd, *args, timeout=timeout)


# ── Recorder (main.py subprocess) ────────────────────────────────────────────

def recorder_python(settings: dict) -> str:
    # The macOS app pins its bundled interpreter, whatever a shared settings.json says.
    return os.environ.get("VOICE_REC_RECORDER_PYTHON") or settings.get("recorder_python") or sys.executable


# The models loaded faster than this: start capturing without asking (see --wait-go)
READY_AUTO_GO_SEC = 4.0


class Recorder:
    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.state = "idle"     # idle | loading | ready | listening | stopping | finalizing | error
        self.detail = ""
        self.project = ""
        self.sid = ""
        self.transcript_path = ""
        self.started_at = 0.0
        self.listening_at = 0.0
        self.lang = ""
        self.engine = ""
        self.diarization = False
        self.error = ""
        self.download_error = ""   # a model that could not be downloaded (shown instead of the exit code)
        self.entries: list[dict] = []
        self.shots: dict[str, dict] = {}
        self.log: deque[str] = deque(maxlen=400)
        self.activity = {"kind": "idle"}
        self.options: dict = {}
        self.deleted: set[str] = set()
        self.muted = False
        self.auto_frames = False
        self.frame_stats: dict = {}
        self.source_progress: dict | None = None    # file: {"done", "total", "offset", "pos" (in the file), "paused"}
        self.source_done: dict | None = None        # file: {"start", "position", "complete"} when it ended
        self.stop_requested = False                 # Stop was pressed (not a crash)
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
            "engine": self.engine, "diarization": self.diarization, "error": self.error, "active": self.active,
            "activity": self.activity, "options": self.options, "pipeline": self.pipeline,
            "muted": self.muted, "auto_frames": self.auto_frames, "frame_stats": self.frame_stats,
            "minutes": self.options.get("minutes"),
            "source": self.options.get("source") or "mic", "source_app": self.options.get("source_app") or "",
            "source_name": self.options.get("source_name") or "", "source_progress": self.source_progress,
            "source_done": self.source_done,
        }

    def live_session(self) -> dict:
        return {
            "project": self.project, "sid": self.sid, "live": True,
            "transcript_path": self.transcript_path, "entries": self.entries,
            "deleted": sorted(self.deleted),
            "screenshots": list(self.shots.values()), "diarized": False,
            "manifest": S.public_manifest(self.project, self.sid, S.load_manifest(self.project, self.sid))
            if self.sid else {"versions": []},
            "visibility": S.load_visibility(self.project, self.sid) if self.sid
            else {"lines": [], "shots": [], "frames": []},
        }

    def set_state(self, state: str, detail: str = ""):
        self.state, self.detail = state, detail
        hub.emit({"type": "recorder", **self.snapshot()})

    async def start(self, opts: dict):
        if self.active and self.state == "ready":
            # Models are already loaded (the "ready" prompt was closed): start right away,
            # with the live-editable options as they are now.
            for k in ("minutes", "pause"):
                if opts.get(k):
                    self.options[k] = opts[k]
            await self._send(f"MINUTES {float(self.options.get('minutes') or 180)}")
            self.pipeline["pause"] = float(self.options.get("pause") or 1.5)
            await self._send(f"PAUSE {self.pipeline['pause']}")
            await self.go()
            return
        if self.active:
            raise HTTPException(409, "A recording is already running")
        project = S.safe_name(opts.get("project", ""))
        settings = S.load_settings()
        python = recorder_python(settings)
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
        engine = str(opts.get("engine") or "whisper")
        if engine not in ENGINES:
            raise HTTPException(400, f"Unknown engine: {engine}")
        if engine == "gigaam" and str(opts.get("lang") or "auto") == "en":
            raise HTTPException(400, "GigaAM recognizes Russian only; pick Auto/RU or another engine")
        if engine != "whisper":
            cmd += ["--engine", engine]
        if str(opts.get("source") or "mic") not in FILE_SOURCES:
            cmd.append("--wait-go")         # a file has nobody to wait for
        self.engine = engine
        speakers = str(opts.get("speakers") or "off")
        if speakers != "off":
            cmd += ["--speakers", speakers]
            self.diarization = True
        if opts.get("save_audio"):
            cmd.append("--save-audio")
        if opts.get("auto_frames"):
            cmd += ["--auto-frames", "1"]
        if not settings.get("frames_l1", True):
            cmd.append("--no-frames-l1")
        source = str(opts.get("source") or "mic")
        if source not in SOURCES:
            raise HTTPException(400, f"Unknown source: {source}")
        if source in ("app", "app+mic"):
            app_id = str(opts.get("source_app") or "").strip()
            if not app_id:
                raise HTTPException(400, "Pick the app whose audio to transcribe")
            if not native_helpers.audio_cmd():
                raise HTTPException(400, f"App audio needs audio_helper ({native_helpers.audio_build_hint()})")
            cmd += ["--source", f"{source}:{app_id}"]
        elif source in FILE_SOURCES:
            path = audio_file_path(project, str(opts.get("source_file") or ""))
            cmd += ["--source", f"file:{path}"]
            if not opts.get("batch"):
                # A file's timestamps start at 00:00:00: a new session (the batch queue continues
                # its own interrupted session with --start where it stopped).
                opts["continue_sid"] = ""
            for k in ("start", "end"):
                try:
                    v = float(opts.get(k) or 0)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"Bad {k} position")
                if v > 0:
                    cmd += [f"--{k}", f"{v:.2f}"]
            if source == "video-rt":
                cmd.append("--realtime")
        continue_sid = (opts.get("continue_sid") or "").strip()
        if continue_sid:
            if not S.transcript_path(project, continue_sid).exists():
                raise HTTPException(404, "The session to continue was not found")
            cmd += ["--continue-session", S.safe_name(continue_sid)]
            # Show what's already there; new lines are appended live.
            existing = S.load_session(project, continue_sid)
            self.sid = continue_sid
            self.transcript_path = existing["transcript_path"]
            self.entries = existing["entries"]
            self.shots = {s["path"]: s for s in existing["screenshots"]}
        self.pipeline["pause"] = float(opts.get("pause") or 1.5)

        cmd += ["--device", settings.get("device") or "auto"]
        self.log.append("$ " + " ".join(cmd[1:]))
        # Own session / process group: Ctrl+C in the server's console doesn't reach it
        # (Windows: no SIGINT for children, stop() sends STOP on stdin instead).
        detach = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                  if native_helpers.IS_WINDOWS else {"start_new_session": True})
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(S.DATA_ROOT), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, **detach,
            env=native_helpers.child_env(), limit=STREAM_LIMIT)
        if not opts.get("batch"):       # the queue's runs don't change the UI's choices
            S.save_settings({"last_project": project, **{k: opts[k] for k in
                             ("engine", "lang", "speakers", "minutes", "pause", "save_audio", "auto_frames",
                              "source", "source_app")
                             if k in opts}})
        self.set_state("loading", "Starting recognizer")
        loop = asyncio.get_running_loop()
        loop.create_task(self._read_output())
        loop.create_task(self._tail_events(events_path))
        loop.create_task(self._wait_exit(events_path))

    def interrupt(self):
        """Same as Ctrl+C in the console: finish the phrase, diarize, save."""
        if native_helpers.IS_WINDOWS:            # no SIGINT for child processes on Windows
            self.proc.stdin.write(b"STOP\n")
        else:
            self.proc.send_signal(signal.SIGINT)

    async def _send(self, line: str):
        self.proc.stdin.write(line.encode() + b"\n")
        await self.proc.stdin.drain()

    async def go(self):
        """Models are loaded (state "ready"): start capturing."""
        if not self.active or self.state != "ready":
            raise HTTPException(409, "The recognizer is not waiting to start")
        await self._send("GO")
        self.set_state("loading", "Starting capture")

    async def set_minutes(self, minutes: float):
        self.options["minutes"] = minutes
        if self.active:
            await self._send(f"MINUTES {minutes}")
        hub.emit({"type": "recorder", **self.snapshot()})

    async def stop(self):
        if not self.active:
            return
        self.stop_requested = True
        self.interrupt()
        if self.state in ("loading", "ready", "listening"):
            self.set_state("stopping", "Stopping")

    async def snap(self, at: float | None = None):
        """A screenshot; for a video file the frame at *at* (file position, e.g. the player's)."""
        if not self.active or self.state != "listening":
            raise HTTPException(409, "Not recording")
        self.proc.stdin.write(b"SNAP\n" if at is None else f"SNAP {at:.3f}\n".encode())
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

    async def set_muted(self, muted: bool):
        if not self.active or self.state != "listening":
            raise HTTPException(409, "Not recording")
        self.proc.stdin.write(b"MUTE\n" if muted else b"UNMUTE\n")
        await self.proc.stdin.drain()

    async def cut(self):
        """End the phrase being recorded now instead of waiting for the pause."""
        if not self.active or self.state != "listening":
            raise HTTPException(409, "Not recording")
        self.proc.stdin.write(b"CUT\n")
        await self.proc.stdin.drain()

    async def set_auto_frames(self, on: bool):
        if not self.active or self.state != "listening":
            raise HTTPException(409, "Not recording")
        self.proc.stdin.write(b"AUTO ON\n" if on else b"AUTO OFF\n")
        await self.proc.stdin.drain()

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
            if st == "ready" and time.time() - self.started_at < READY_AUTO_GO_SEC:
                # Loaded quickly: the user is still at the Start button, no need to ask.
                await self._send("GO")
                return
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
        elif t == "source_progress":
            self.source_progress = {k: ev.get(k, 0) for k in ("done", "total", "offset", "pos", "paused")}
            hub.emit({"type": "source_progress", **self.source_progress})
        elif t == "source_done":
            self.source_done = {k: ev.get(k) for k in ("start", "position", "complete")}
            self.source_done["file"] = self.options.get("source_file") or ""
        elif t == "minutes":
            self.options["minutes"] = ev.get("minutes")
            hub.emit({"type": "recorder", **self.snapshot()})
        elif t == "muted":
            self.muted = bool(ev.get("muted"))
            hub.emit({"type": "recorder", **self.snapshot()})
        elif t == "segment":
            entry = {"time": ev["time"], "kind": "speech", "speaker": ev.get("speaker"), "text": ev["text"]}
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
        elif t == "auto_frames":
            self.auto_frames = bool(ev.get("on"))
            hub.emit({"type": "recorder", **self.snapshot()})
        elif t == "frame_scene":
            scene = {k: v for k, v in ev.items() if k not in ("type", "ts")}
            scene["url"] = S.file_url(ev["path"])
            scene["last_url"] = S.file_url(ev["last_path"]) if ev.get("last_path") else None
            hub.emit({"type": "frame", "project": self.project, "sid": self.sid, "scene": scene})
        elif t in ("frame_ocr", "frame_ocr_error"):
            if ev.get("id") is not None:
                hub.emit({"type": "frame", "project": self.project, "sid": self.sid,
                          "scene": {"id": ev["id"], "ocr_ready": t == "frame_ocr"}})
        elif t == "frame_stats":
            self.frame_stats = {k: v for k, v in ev.items() if k not in ("type", "ts")}
            hub.emit({"type": "frame_stats", "project": self.project, "sid": self.sid, **self.frame_stats})
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
        elif t == "download":
            dl = {k: ev.get(k) for k in ("label", "done_mb", "total_mb", "finished", "error")}
            hub.emit({"type": "download", **dl})
            if dl.get("error"):
                self.download_error = f"Could not download {dl['label']}: {dl['error']}"
            if self.state == "loading" and not dl["finished"]:
                total = f" of ~{dl['total_mb']:.0f}" if dl.get("total_mb") else ""
                self.set_state("loading", f"Downloading {dl['label']} (first use): {dl['done_mb']:.0f}{total} MB")

    def _emit_shot(self, shot: dict, new: bool = False):
        if not shot.get("deleted"):
            hub.emit({"type": "shot", "project": self.project, "sid": self.sid, "shot": shot, "new": new})

    def _add_entry(self, entry: dict):
        if entry.get("kind") == "screenshot" and entry.get("path") in self.deleted:
            entry["deleted"] = True
        entry["key"] = S.line_key(entry)
        self.entries.append(entry)
        hub.emit({"type": "entry", "project": self.project, "sid": self.sid,
                  "idx": len(self.entries) - 1, "entry": entry})

    async def _wait_exit(self, events_path: Path):
        rc = await self.proc.wait()
        await asyncio.sleep(0.5)  # let the tail drain
        project, sid = self.project, self.sid
        if not self.listening_at:
            sid = ""        # cancelled while loading / waiting for GO: nothing was recorded
            if rc in (0, -signal.SIGINT):
                events_path.unlink(missing_ok=True)
        if sid:
            sdir = S.session_dir(project, sid)
            sdir.mkdir(parents=True, exist_ok=True)
            try:
                events_path.replace(sdir / "events.jsonl")
            except OSError:
                pass
        if rc not in (0, -signal.SIGINT) and self.state not in ("finalizing",):
            tail = "\n".join(list(self.log)[-12:])
            self.error = self.download_error or f"Recognizer exited with code {rc}"
            self.set_state("error", tail)
        else:
            self.set_state("idle", "Session finished")
        if sid:
            hub.emit({"type": "session_changed", "project": project, "sid": sid, "finished": True})
            has_speech = S.transcript_path(project, sid).exists() and any(
                e["kind"] == "speech" for e in S.parse_transcript(S.read_transcript(project, sid)))
            if S.load_settings().get("auto_final_beautify") and has_speech and not self.options.get("batch"):
                # Ask in the UI instead of starting a long run on its own; "Not now" leaves
                # the Beautify button as the way to start it later.
                hub.emit({"type": "offer_beautify", "project": project, "sid": sid})


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
               "lines": spec["lines"], "t_last": spec["t_last"], "shots": spec["shot_count"],
               "frames": spec["frame_count"], "log_url": S.file_url(spec["log"]), "spec": spec}
        self.jobs[key] = job
        self.step(job, {"update": "Updating the previous version",
                        "full": "Rebuilding the full document",
                        "final": "Building the final version"}[spec["mode"]])
        asyncio.get_running_loop().create_task(self._run(job))
        return self.public(job)

    async def _screen_notes(self, job: dict, spec: dict, log):
        n = len(spec.get("image_items") or [])
        if not n:
            return
        self.step(job, f"Condensing what was on screen ({n} images)")
        notes = await asyncio.to_thread(beautify.screen_notes, spec, log)
        job["steps"][-1]["text"] = (f"Screen notes: {n} images → {len(notes):,} characters" if notes
                                    else "Screen notes failed: using the screenshots' OCR text (see Log)")
        self.broadcast(job)

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
            if job["spec"].get("cancel"):
                job["spec"]["cancel"].set()     # API engine: the request stops at its next piece
        if proc and proc.returncode is None:
            proc.terminate()

    async def _run(self, job: dict):
        spec = job["spec"]
        key = (job["project"], job["sid"])
        result: dict = {}
        api_info: dict = {}
        stderr_tail = ""
        log = beautify.RunLog(spec["log"])
        try:
            if spec["settings"]["beautify_engine"] == "api":
                spec["cancel"] = threading.Event()
                log.header(spec, "api", llm_api.config(spec["settings"])["model"])
                await self._screen_notes(job, spec, log)
                self.step(job, f"Writing the document with {llm_api.status(spec['settings'], True)['label']}")
                loop = asyncio.get_running_loop()
                last = [0.0]

                def progress(chars: int, note: str = ""):
                    if time.time() - last[0] < 1.5:
                        return
                    last[0] = time.time()

                    def show():
                        text = f"{note or 'Writing the document'} · {chars:,} characters"
                        if job["steps"][-1].get("note") == note:
                            job["steps"][-1]["text"] = text
                        else:      # a new part: its own step, so the list shows how the parts went
                            job["steps"].append({"t": round(time.time() - job["started"], 1), "text": text, "note": note})
                        self.broadcast(job)
                    loop.call_soon_threadsafe(show)
                api_info = await asyncio.to_thread(beautify.run_api, spec, progress, log)
                ok = True
            else:
                cmd = beautify.claude_command(spec)
                log.header(spec, "claude", spec["settings"]["beautify_model"], cmd)
                await self._screen_notes(job, spec, log)
                proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(spec["cwd"]), stdin=asyncio.subprocess.PIPE,
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
                    log.event(ev)
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
        if stderr_tail.strip():
            log.write("stderr: " + stderr_tail.strip())
        if spec["settings"]["beautify_engine"] != "api" and not result:
            log.summary()
        if job.get("cancelled"):
            log.write("Cancelled")

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
            cost=result.get("total_cost_usd"), error=error, **api_info)
        if rec.get("frames_added"):
            log.write(f"Inserted {rec['frames_added']} auto frames into the document")
        log.write(f"Status: {rec['status']} {rec.get('error', '')}".rstrip())
        log.close()
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


# ── Batch queue (batch.py): YouTube → download → transcribe → Beautify ──────

class ServerRunner:
    """Batch stages through the recorder and the Beautify jobs, so they show in the main UI."""

    async def recognize(self, task, stage, file, update):
        while recorder.active:
            update(note="Waiting for the recognizer: a recording is running", line="")
            await asyncio.sleep(2)
        o = batch.recognize_args(task, stage, S.load_settings())
        if o["continue_sid"] and not S.transcript_path(task["project"], o["continue_sid"]).exists():
            o["continue_sid"] = ""
        await recorder.start({
            "project": task["project"], "engine": o["engine"], "lang": o["lang"], "speakers": o["speakers"],
            "pause": o["pause"], "minutes": 600, "source": "video", "source_file": file.name,
            "source_name": task.get("title") or file.name, "auto_frames": o["auto_frames"],
            "start": o["start"], "continue_sid": o["continue_sid"], "batch": task["id"]})
        proc = recorder.proc
        update(note="", line="Starting the recognizer")
        try:
            while proc.returncode is None:
                try:
                    await asyncio.wait_for(asyncio.shield(proc.wait()), 1.0)
                except asyncio.TimeoutError:
                    pass
                if recorder.sid:
                    stage["sid"] = recorder.sid
                p = recorder.source_progress
                if recorder.state == "listening" and p and p.get("total"):
                    update(progress=round(100 * p["done"] / p["total"], 1), note="paused" if p.get("paused") else "",
                           line=f"Transcribing {batch.fmt_hms(p.get('pos'))} of "
                                f"{batch.fmt_hms((p.get('offset') or 0) + p['total'])}")
                elif recorder.state in ("loading", "stopping", "finalizing"):
                    update(line=recorder.detail or recorder.state.capitalize())
        except asyncio.CancelledError:
            if proc.returncode is None:
                recorder.interrupt()
                await proc.wait()
            await asyncio.sleep(1.2)
            if recorder.source_done:
                stage["position"] = recorder.source_done.get("position")
            raise
        await asyncio.sleep(1.2)        # _wait_exit: the event tail drains, the state settles
        done = recorder.source_done or {}
        res = {"sid": recorder.sid or stage.get("sid"), "position": done.get("position"),
               "complete": bool(done.get("complete"))}
        if not res["complete"] and recorder.stop_requested:
            pos = res["position"] or stage.get("position")
            res["error"] = (f"Stopped from the main window at {batch.fmt_hms(pos)}: restart to continue from there"
                            if pos else "Stopped from the main window before the transcription started")
        elif recorder.state == "error" and not res["complete"]:
            res["error"] = f"{recorder.error}\n{recorder.detail}".strip()
        return res

    async def beautify(self, task, stage, sid, update):
        key = (task["project"], sid)
        while jobs.running(*key):
            update(note="Waiting: a Beautify of this session is already running")
            await asyncio.sleep(2)
        try:
            await jobs.start(task["project"], sid, "final")
        except HTTPException as e:
            return {"ok": False, "error": str(e.detail)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        update(note="")
        try:
            while True:
                job = jobs.jobs.get(key)
                if not job or job["status"] != "running":
                    break
                steps = job.get("steps") or []
                update(line=steps[-1]["text"] if steps else "Beautify",
                       note=f"{job.get('chars', 0):,} characters written" if job.get("chars") else "")
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            await jobs.cancel(*key)
            raise
        job = jobs.jobs.get(key) or {}
        return {"ok": job.get("status") == "done", "v": job.get("v"), "error": job.get("error") or job.get("status")}


def _batch_public(task: dict, log_lines: int = 40) -> dict:
    out = {**task, "state": batch.task_state(task), "stages": {}}
    for name, s in task["stages"].items():
        out["stages"][name] = {**s, "log": (s.get("log") or [])[-log_lines:]} if "log" in s else dict(s)
    return out


def _batch_changed(task: dict, removed: bool = False):
    hub.emit({"type": "batch_removed", "id": task["id"]} if removed
             else {"type": "batch_task", "task": _batch_public(task)})


batch_queue: batch.Queue | None = None
batch_orch: batch.Orchestrator | None = None


# ── Chat about the transcript ────────────────────────────────────────────────

CHAT_SYSTEM = """You are the assistant inside a live transcription app. The user asks about the transcript \
of a meeting or walkthrough, which may still be recording. Answer concisely in the language of the question, \
using Markdown. Cite moments with their [HH:MM:SS] timestamps. The transcript is raw speech-to-text, so expect \
recognition errors and interpret sensibly. Lines like [screenshot: path] point to screen captures you can view \
with the Read tool when relevant. If a project knowledge base (claude-context MCP) is available, use it when the \
question needs background. Never modify files."""


CHAT_SYSTEM_API = """You are the assistant inside a live transcription app. The user asks about the transcript \
below, of a meeting or walkthrough that may still be recording. Answer concisely in the language of the question, \
using Markdown. Cite moments with their [HH:MM:SS] timestamps. The transcript is raw speech-to-text, so expect \
recognition errors and interpret sensibly; lines like [screenshot: path] mark screen captures (you cannot see them)."""


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
        ext_files = [str(p) for p in external.for_session(project, sid)]
        ext_hash = hashlib.md5("\n".join(ext_files).encode()).hexdigest()
        if ext_files and data.get("ext_hash") != ext_hash:
            notes_note += ("External transcript(s) of the same meeting (e.g. Teams; reliable speaker names, often "
                           "misrecognized terms). Read them when a question is about who said what:\n"
                           + "".join(f"- {p}\n" for p in ext_files) + "\n")
        data["ext_hash"] = ext_hash
        if prefix_ok:
            delta = transcript[data["sent_len"]:]
            body = (f"New transcript lines since my last message ({status}):\n<transcript_update>\n{delta}"
                    f"</transcript_update>\n\n" if delta.strip() else f"(No new transcript lines; {status}.)\n\n")
        else:
            # First question, or the transcript was rewritten (e.g. speakers assigned): send it in full.
            intro = "Updated full transcript (it was re-processed)" if data["claude_session"] else "Transcript"
            body = (f"{intro} - file: {path} ({status}).\n"
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
        ctx = Path(psettings["context_dir"]).expanduser() if psettings["context_dir"] else S.DATA_ROOT
        cwd = ctx if ctx.is_dir() else S.DATA_ROOT

        def push(**kw):
            hub.emit({"type": "chat_update", "project": project, "sid": sid, "id": msg_id, **kw})

        if settings.get("beautify_engine") == "api":
            await self._run_api(project, sid, data, msg, question, settings, push)
            return
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

    async def _run_api(self, project, sid, data, msg, question, settings, push):
        """Chat through an API model (no tools): the transcript, notes and external
        transcripts go into the system prompt, earlier turns as messages."""
        key = (project, sid)
        try:
            live = recorder.active and recorder.project == project and recorder.sid == sid
            system = CHAT_SYSTEM_API + f"\n\nTranscript ({'still recording' if live else 'recording finished'}):\n" \
                f"<transcript>\n{S.read_transcript(project, sid)}</transcript>"
            notes = S.load_notes(project).strip()
            if notes:
                system += f"\n\nThe user's notes and corrections (authoritative):\n<notes>\n{notes}\n</notes>"
            for p in external.for_session(project, sid):
                system += (f"\n\nExternal transcript of the same meeting ({p.name}; reliable speaker names, "
                           f"often misrecognized terms):\n<external>\n{p.read_text(encoding='utf-8', errors='replace')}"
                           f"\n</external>")
            history = [{"role": m["role"], "content": m["text"]} for m in data["messages"]
                       if m["id"] != msg["id"] and m.get("text")
                       and (m["role"] == "user" or m.get("status", "done") == "done")]
            loop = asyncio.get_running_loop()

            def delta(d):
                def apply():
                    msg["text"] += d
                    msg["status"] = "streaming"
                    push(delta=d, status="streaming")
                loop.call_soon_threadsafe(apply)
            cfg = llm_api.config(settings)
            await asyncio.to_thread(llm_api.stream, cfg, [{"role": "system", "content": system}, *history], delta)
            await asyncio.sleep(0)   # let the last deltas land
            msg["status"] = "done"
        except Exception as e:
            msg["status"] = "error"
            msg["error"] = llm_api.explain(e) if not isinstance(e, RuntimeError) else str(e)
        finally:
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
    loop = asyncio.get_running_loop()
    # Models the server itself loads (CLIP for "hide similar") report downloads too.
    downloads.set_reporter(lambda ev: loop.call_soon_threadsafe(hub.emit, {"type": "download", **ev}))
    global batch_queue, batch_orch
    batch_queue = batch.Queue(S.WEBUI_DIR / "batch.json", on_change=_batch_changed)
    batch_orch = batch.Orchestrator(batch_queue, ServerRunner(),
                                    parallel=int(S.load_settings().get("batch_parallel") or batch.DEFAULT_PARALLEL),
                                    on_external=lambda project: hub.emit({"type": "external", "project": project,
                                                                          **_external_state(project)}))
    batch_task = loop.create_task(batch_orch.run())
    yield
    batch_task.cancel()
    for tid in {tid for tid, _ in batch_orch.running}:
        batch_orch.cancel(tid)
    if recorder.active:
        print("Stopping the running recording (finalizing, please wait)...")
        recorder.interrupt()
        try:
            await asyncio.wait_for(recorder.proc.wait(), 600)
        except asyncio.TimeoutError:
            recorder.proc.kill()
    for p in list(jobs.procs.values()) + list(chat.procs.values()):
        if p.returncode is None:
            p.terminate()


# ── Local-only guard ─────────────────────────────────────────────────────────
# The API has no login: it trusts whoever can reach 127.0.0.1. Browsers let any web page
# send requests there (CSRF: start a recording, swap the LLM endpoint to steal API keys,
# open the WebSocket to listen in) or reach it under its own domain (DNS rebinding).
# So: the Host must be ours, and a browser's Origin, when sent, must be the page itself.

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}    # + --host / --allow-host


def _host_only(hostport: str) -> str:
    hostport = hostport.strip().lower()
    if hostport.startswith("["):                      # [::1]:8765
        return hostport[1:].split("]", 1)[0]
    return hostport.rsplit(":", 1)[0] if hostport.count(":") == 1 else hostport


def _forbidden(scope) -> str | None:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    host = headers.get("host", "")
    if _host_only(host) not in ALLOWED_HOSTS:
        return f"Host {host!r} is not allowed"
    origin = headers.get("origin")
    if origin is not None:
        o = origin.lower()
        if "://" not in o or o.split("://", 1)[1].rstrip("/") != host.lower():
            return f"Origin {origin!r} is not allowed"
    return None


class LocalOnly:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            reason = _forbidden(scope)
            if reason:
                print(f"[server] refused {scope['type']} {scope.get('path')}: {reason}", file=sys.stderr)
                if scope["type"] == "websocket":
                    await receive()                       # websocket.connect
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": reason.encode()})
                return
        await self.app(scope, receive, send)


app = FastAPI(lifespan=lifespan)
app.add_middleware(LocalOnly)
S.PROJECTS_DIR.mkdir(exist_ok=True)
app.mount("/files", StaticFiles(directory=str(S.PROJECTS_DIR)), name="files")
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    # Make the browser revalidate the UI files (cheap 304 via ETag), so an updated
    # app.js/style.css shows up without a hard reload.
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    if path.startswith("/files/"):
        # Recordings, Beautify output and uploads are data: their HTML/SVG must never run
        # script on the app's origin (an injected <script> would have the whole API).
        response.headers["Content-Security-Policy"] = "script-src 'none'; object-src 'none'; base-uri 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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
    settings = S.load_settings()
    claude = bool(claude_cli.claude_binary())
    return {"claude": claude, "helper": bool(native_helpers.screenshot_cmd()), "platform": sys.platform,
            "models": downloads.engine_models(),
            "ai": llm_api.status(settings, claude), "bundled_python": bool(os.environ.get("VOICE_REC_RECORDER_PYTHON")),
            "settings": S.load_settings(), "recorder": recorder.snapshot()}


@app.get("/api/ai")
async def ai_get():
    settings = S.load_settings()
    claude = bool(claude_cli.claude_binary())
    return {**llm_api.public(settings), **llm_api.status(settings, claude), "claude": claude}


@app.post("/api/ai")
async def ai_set(data: dict = Body(...)):
    """{engine: claude|api, provider, model, base_url, api_key?, clear_key?}"""
    engine = data.get("engine", "claude")
    if engine not in ("claude", "api"):
        _err(ValueError("Bad engine"))
    upd = {"beautify_engine": engine, "ai_setup_done": True}
    if engine == "api":
        provider = data.get("provider") or "gemini"
        if provider not in llm_api.PROVIDERS:
            _err(ValueError("Unknown provider"))
        upd.update(api_provider=provider, api_model=(data.get("model") or "").strip(),
                   api_base_url=llm_api.normalize_base_url(provider, data.get("base_url") or ""))
        if data.get("api_key"):
            llm_api.set_key(provider, data["api_key"])
        elif data.get("clear_key"):
            llm_api.set_key(provider, None)
    S.save_settings(upd)
    return await ai_get()


@app.get("/api/ai/vision")
async def ai_vision_get():
    settings = S.load_settings()
    cfg = image_notes.vision_config(settings)
    return {"provider": settings.get("vision_provider") or "", "model": settings.get("vision_model") or "",
            "providers": {p: {"name": llm_api.PROVIDERS[p]["name"], "model": m,
                              "key": llm_api.mask(llm_api.stored_key(p)) if llm_api.stored_key(p) else "",
                              "env_key": bool(llm_api.env_key(p))}
                          for p, m in image_notes.VISION_MODELS.items()},
            "effective": {"provider": cfg["provider"], "model": cfg["model"]},
            "problem": image_notes.vision_problem(cfg)}


@app.post("/api/ai/vision")
async def ai_vision_set(data: dict = Body(...)):
    """{provider: ''|openai|gemini|anthropic|openrouter, model, api_key?}"""
    provider = data.get("provider") or ""
    if provider and provider not in image_notes.VISION_MODELS:
        _err(ValueError("This provider can't describe images here"))
    if provider and data.get("api_key"):
        llm_api.set_key(provider, data["api_key"])
    S.save_settings({"vision_provider": provider, "vision_model": (data.get("model") or "").strip()})
    return await ai_vision_get()


@app.post("/api/ai/test-claude")
async def ai_test_claude():
    """Run a tiny `claude -p` exactly as Beautify does, and report what it says."""
    exe = claude_cli.claude_binary()
    if not exe:
        return {"ok": False, "error": "Claude Code (the claude CLI) is not installed"}
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "-p", "Reply with exactly: OK", "--model", "haiku", cwd=str(S.DATA_ROOT),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 90)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "error": "No answer in 90 s", "path": exe}
    text = out.decode("utf-8", "replace").strip()
    if proc.returncode == 0 and text:
        return {"ok": True, "reply": text[:60], "path": exe}
    return {"ok": False, "error": text[-400:] or f"exit code {proc.returncode}", "path": exe}


@app.post("/api/ai/test")
async def ai_test(data: dict = Body(...)):
    provider = data.get("provider") or "gemini"
    if provider not in llm_api.PROVIDERS:
        _err(ValueError("Unknown provider"))
    cfg = llm_api.config({"api_provider": provider, "api_model": data.get("model") or "",
                          "api_base_url": data.get("base_url") or ""})
    if data.get("api_key"):
        cfg["key"] = data["api_key"].strip()
    if not cfg["key"] and provider != "custom":
        return {"ok": False, "error": "Enter the API key"}
    return await asyncio.to_thread(llm_api.test, cfg)


@app.get("/api/check-recorder")
async def check_recorder(engine: str = "whisper"):
    python = recorder_python(S.load_settings())
    asr_module = "faster_whisper" if engine == "whisper" else "onnx_asr"
    rc, out, err = await run_cmd(python, "-c", f"import {asr_module}, sounddevice, easyocr, torch; print('ok')")
    return {"ok": rc == 0, "python": python, "error": (err or out).strip()[-300:]}


@app.get("/api/settings")
async def get_settings():
    return S.load_settings()


@app.post("/api/settings")
async def set_settings(data: dict = Body(...)):
    try:
        return S.save_settings(data)
    except ValueError as e:
        _err(e)


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
    try:
        return S.save_project_settings(project, data)
    except ValueError as e:
        _err(e)


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
    ai = llm_api.status(S.load_settings(), bool(claude_cli.claude_binary()))
    if not ai["ready"]:
        _err(RuntimeError(f"Chat is not set up: {ai['problem']} (Settings → AI)"))
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


@app.post("/api/projects/{project}/notes/speaker")
async def notes_speaker(project: str, data: dict = Body(...)):
    quote, speaker = (data.get("quote") or "").strip(), (data.get("speaker") or "").strip()
    if not quote or not speaker:
        _err(ValueError("Both the quote and the speaker are needed"))
    text = S.add_speaker(project, quote, speaker, (data.get("time") or "").strip())
    hub.emit({"type": "notes", "project": project, "text": text})
    return {"text": text}


# ── External transcripts (e.g. Teams) of the same meeting ───────────────────

def _external_state(project: str) -> dict:
    files = external.public(project)
    names = S.known_speakers(S.load_notes(project)) + [n for f in files for n in f.get("speakers", [])]
    return {"files": files, "speakers": list(dict.fromkeys(names))}


# ── Audio sources: apps playing audio, uploaded audio files ─────────────────

@app.get("/api/audio/apps")
async def audio_apps():
    """Apps connected to Core Audio (audio_helper --list), the ones playing sound first.
    On Windows: one entry, the system audio (WASAPI loopback)."""
    helper = native_helpers.audio_cmd()
    if not helper:
        return {"available": False, "apps": [], "error": f"audio_helper is missing ({native_helpers.audio_build_hint()})"}
    try:
        rc, out, err = await run_cmd(*helper, "--list", timeout=15)
        if rc != 0:
            raise RuntimeError(err.strip() or f"exit code {rc}")
        apps = [a for a in json.loads(out) if not a["bundle"].startswith(("com.voicerec", "com.github.Electron"))]
        return {"available": True, "apps": apps, "error": ""}
    except Exception as e:
        return {"available": False, "apps": [], "error": str(e)}


AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff", ".caf",
              ".wma", ".webm", ".mp4", ".mov", ".mkv", ".m4v", ".avi"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def audio_dir(project: str) -> Path:
    return S.project_dir(project) / "audio"


def audio_file_path(project: str, name: str) -> Path:
    path = audio_dir(project) / Path(name).name
    if not name or name != path.name or name.startswith(".") or not path.is_file():
        raise HTTPException(404, f"Audio file not found: {name}")
    return path.resolve()


def _audio_files(project: str) -> list[dict]:
    d = audio_dir(project)
    files = [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")] if d.is_dir() else []
    return [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime,
             "video": p.suffix.lower() in VIDEO_EXTS}
            for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]


MEDIA_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
               ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
               ".wav": "audio/wav", ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg",
               ".opus": "audio/ogg"}


@app.get("/api/projects/{project}/audio/{name}/media")
async def audio_media(project: str, name: str, request: Request):
    """The file for the player, with Range support (seeking; Starlette 0.38 has none)."""
    path = audio_file_path(project, name)
    size = path.stat().st_size
    ctype = MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    m = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("range", "").strip())
    if not m or (not m.group(1) and not m.group(2)):
        return FileResponse(path, media_type=ctype, headers={"Accept-Ranges": "bytes"})
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:                                   # "bytes=-N": the last N bytes
        start, end = max(0, size - int(m.group(2))), size - 1
    if start >= size or start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(left, 1 << 20))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(body(), status_code=206, media_type=ctype, headers={
        "Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}",
        "Content-Length": str(end - start + 1)})


@app.get("/api/projects/{project}/audio")
async def audio_list(project: str):
    return {"files": _audio_files(project)}


@app.put("/api/projects/{project}/audio")
async def audio_upload(project: str, request: Request, name: str):
    """Store an audio file to transcribe (body = the file) in projects/<p>/audio/."""
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    if ext not in AUDIO_EXTS:
        _err(ValueError(f"Not an audio file this app can read: {name}"))
    clean = re.sub(r"[^\w.\- ]+", "_", stem).strip(" ._") or "audio"
    d = audio_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    path, n = d / f"{clean}{ext}", 1
    while path.exists():
        n += 1
        path = d / f"{clean} ({n}){ext}"
    tmp = path.with_name("." + path.name + ".part")
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return {"name": path.name, "files": _audio_files(project)}


@app.get("/api/projects/{project}/external")
async def external_list(project: str):
    return _external_state(project)


@app.put("/api/projects/{project}/external")
async def external_upload(project: str, request: Request, name: str, sid: str | None = None):
    try:
        await asyncio.to_thread(external.add, project, name, await request.body(), sid or None)
    except (ValueError, KeyError, zipfile.BadZipFile) as e:
        _err(ValueError(f"Can't read {name}: {e}"))
    state = _external_state(project)
    hub.emit({"type": "external", "project": project, **state})
    return state


@app.patch("/api/projects/{project}/external/{name}")
async def external_bind(project: str, name: str, data: dict = Body(...)):
    try:
        external.set_session(project, name, data.get("sid") or None)
    except ValueError as e:
        _err(e)
    state = _external_state(project)
    hub.emit({"type": "external", "project": project, **state})
    return state


@app.delete("/api/projects/{project}/external/{name}")
async def external_delete(project: str, name: str):
    try:
        external.remove(project, name)
    except ValueError as e:
        _err(e)
    state = _external_state(project)
    hub.emit({"type": "external", "project": project, **state})
    return state


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
    ai = llm_api.status(S.load_settings(), bool(claude_cli.claude_binary()))
    if not ai["ready"]:
        _err(RuntimeError(f"Beautify is not set up: {ai['problem']} (Settings → AI)"))
    try:
        return await jobs.start(project, sid, mode)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        _err(e)


@app.post("/api/projects/{project}/sessions/{sid}/versions/{v}/embed")
async def beautify_embed(project: str, sid: str, v: int):
    """vN.html → vN-emb.html with the images embedded (one file to share)."""
    try:
        out = await asyncio.to_thread(beautify.embed_version, S.safe_name(project), S.safe_name(sid), v)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"url": S.file_url(out), "name": out.name, "size": out.stat().st_size}


@app.delete("/api/projects/{project}/sessions/{sid}/beautify")
async def beautify_cancel(project: str, sid: str):
    await jobs.cancel(project, sid)
    return {"ok": True}


# Recording

@app.post("/api/reveal")
async def reveal(data: dict = Body(...)):
    """Open the folder of a project file (given by its /files/ URL), with the file selected."""
    url = str(data.get("url") or "")
    if not url.startswith("/files/"):
        raise HTTPException(400, "Not a project file")
    path = (S.PROJECTS_DIR / unquote(url[len("/files/"):].split("?")[0])).resolve()
    if not path.is_relative_to(S.PROJECTS_DIR.resolve()) or not path.exists():
        raise HTTPException(404, "File not found")
    try:
        native_helpers.reveal(path)
    except OSError as e:
        raise HTTPException(500, f"Could not open the folder: {e}")
    return {"ok": True}


@app.get("/prompts")
async def prompts_page():
    return FileResponse(WEB_DIR / "prompts.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{project}/prompts")
async def prompts_get(project: str):
    project = S.safe_name(project)
    return {"project": project, "prompts": beautify.prompts_payload(project)}


@app.put("/api/projects/{project}/prompts")
async def prompts_put(project: str, data: dict = Body(...)):
    """{name: text}: save the project's own version; null (or the default text) resets it."""
    project = S.safe_name(project)
    if not S.project_dir(project).is_dir():
        raise HTTPException(404, "No such project")
    try:
        for name, text in data.items():
            beautify.save_prompt(project, name, None if text is None else str(text))
    except ValueError as e:
        _err(e)
    return {"project": project, "prompts": beautify.prompts_payload(project)}


@app.get("/batch")
async def batch_page():
    return FileResponse(WEB_DIR / "batch.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/batch")
async def batch_list():
    return {"tasks": [_batch_public(t) for t in batch_queue.tasks], "parallel": batch_orch.parallel,
            "ytdlp": await asyncio.to_thread(batch.ytdlp_info), "ytdlp_stale_days": batch.YTDLP_STALE_DAYS}


@app.post("/api/batch/tasks")
async def batch_add(data: dict = Body(...)):
    try:
        added = batch_queue.add(list(data.get("tasks") or []))
    except ValueError as e:
        _err(e)
    batch_orch.kick()
    return {"added": [t["id"] for t in added]}


@app.get("/api/batch/tasks/{task_id}")
async def batch_task(task_id: str):
    task = batch_queue.get(task_id)
    if not task:
        raise HTTPException(404, "No such task")
    return _batch_public(task, log_lines=batch.LOG_LINES)


@app.delete("/api/batch/tasks/{task_id}")
async def batch_delete(task_id: str):
    if not batch_queue.get(task_id):
        raise HTTPException(404, "No such task")
    running = [at for (tid, _), at in batch_orch.running.items() if tid == task_id]
    batch_queue.remove(task_id)         # first: a cancelled stage of a removed task isn't put on hold
    batch_orch.cancel(task_id)
    if running:
        await asyncio.wait(running, timeout=60)
    batch_orch.kick()
    return {"ok": True}


@app.post("/api/batch/tasks/{task_id}/restart")
async def batch_restart(task_id: str, data: dict = Body(default={})):
    stage = data.get("stage")
    if stage is not None and stage not in batch.STAGES:
        _err(ValueError(f"Unknown stage: {stage}"))
    if any(tid == task_id for tid, _ in batch_orch.running if stage in (None, _)):
        raise HTTPException(409, "That stage is running")
    try:
        task = batch_queue.restart(task_id, stage)
    except KeyError:
        raise HTTPException(404, "No such task")
    batch_orch.kick()
    return _batch_public(task)


@app.post("/api/batch/settings")
async def batch_settings(data: dict = Body(...)):
    try:
        n = int(data.get("parallel"))
    except (TypeError, ValueError):
        _err(ValueError("Bad number of parallel downloads"))
    n = max(1, min(10, n))
    S.save_settings({"batch_parallel": n})
    batch_orch.parallel = n
    batch_orch.kick()
    return {"parallel": n}


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


@app.post("/api/record/minutes")
async def record_minutes(data: dict = Body(...)):
    try:
        minutes = round(max(1.0, min(1440.0, float(data.get("minutes")))), 1)
    except (TypeError, ValueError):
        _err(ValueError("Bad auto-stop value"))
    S.save_settings({"minutes": minutes})
    await recorder.set_minutes(minutes)
    return {"minutes": minutes}


@app.post("/api/record/mute")
async def record_mute(data: dict = Body(...)):
    await recorder.set_muted(bool(data.get("muted")))
    return {"ok": True}


@app.post("/api/record/cut")
async def record_cut():
    await recorder.cut()
    return {"ok": True}


@app.post("/api/record/snap")
async def record_snap(data: dict = Body(default={})):
    at = data.get("at")
    try:
        at = None if at is None else max(0.0, float(at))
    except (TypeError, ValueError):
        _err(ValueError("Bad position"))
    await recorder.snap(at)
    return {"ok": True}


@app.post("/api/record/auto")
async def record_auto(data: dict = Body(...)):
    on = bool(data.get("on"))
    await recorder.set_auto_frames(on)
    S.save_settings({"auto_frames": on})
    return {"ok": True}


# Auto frames (autoframes.py / select_frames.py)

@app.post("/api/projects/{project}/sessions/{sid}/describe")
async def describe_image(project: str, sid: str, data: dict = Body(...)):
    """Describe a screenshot or auto frame with the vision model: {path} → {text}.
    The description (<image>.desc.md) replaces its OCR text in Beautify."""
    path = Path(data.get("path") or "").resolve()
    root = S.project_dir(project).resolve()
    if root not in path.parents or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        _err(ValueError("Not an image of this project"))
    try:
        text = await asyncio.to_thread(image_notes.describe, path)
    except Exception as e:
        _err(e)
    hub.emit({"type": "described", "project": project, "sid": sid, "path": str(path)})
    return {"text": text, "path": str(path)}


@app.get("/api/projects/{project}/sessions/{sid}/frames")
async def frames_get(project: str, sid: str):
    return SF.frames_payload(S.safe_name(project), S.safe_name(sid))


@app.post("/api/projects/{project}/sessions/{sid}/frames/select")
async def frames_select(project: str, sid: str, data: dict = Body(default={})):
    project, sid = S.safe_name(project), S.safe_name(sid)
    n = max(1, min(100, int(data.get("n") or 10)))
    await asyncio.to_thread(SF.select, project, sid, n)
    hub.emit({"type": "visibility", "project": project, "sid": sid, "visibility": S.load_visibility(project, sid)})
    return SF.frames_payload(project, sid)


@app.post("/api/projects/{project}/sessions/{sid}/visibility")
async def visibility_set(project: str, sid: str, data: dict = Body(...)):
    """Hide / show a transcript line, a screenshot or an auto frame (left out of Beautify)."""
    project, sid = S.safe_name(project), S.safe_name(sid)
    kind = data.get("kind")
    keys = data.get("keys")
    if kind not in ("line", "shot", "frame") or (not keys and data.get("key") in (None, "")):
        _err(ValueError("Bad visibility change"))
    if keys:
        vis, _ = S.set_hidden_many(project, sid, kind, list(keys), bool(data.get("hidden")))
    else:
        vis = S.set_hidden(project, sid, kind, data["key"], bool(data.get("hidden")))
    hub.emit({"type": "visibility", "project": project, "sid": sid, "visibility": vis})
    return vis


@app.post("/api/projects/{project}/sessions/{sid}/hide-similar")
async def hide_similar(project: str, sid: str, data: dict = Body(...)):
    """Hide a screenshot / auto frame and every picture of this session that looks like it
    (CLIP, similar.py). Returns what was newly hidden, so the UI can undo it."""
    import similar
    project, sid = S.safe_name(project), S.safe_name(sid)
    kind, key = data.get("kind"), data.get("key")
    frames = {g["id"]: g["path"] for g in S.frame_groups(S.load_scenes(project, sid))}
    shots = {Path(s["path"]).name: s["path"] for s in S.load_session(project, sid)["screenshots"]}
    if kind == "frame" and int(key) in frames:
        target = frames[int(key)]
    elif kind == "shot" and key in shots:
        target = shots[key]
    else:
        _err(ValueError("Unknown picture"))
    try:
        hit = await asyncio.to_thread(similar.similar_to, target, {("frame", k): v for k, v in frames.items()}
                                      | {("shot", k): v for k, v in shots.items()})
    except RuntimeError as e:
        _err(e)
    newly = {"frame": [], "shot": []}
    vis = S.load_visibility(project, sid)
    for k in ("frame", "shot"):
        keys = [key_ for kk, key_ in hit if kk == k]
        if keys:
            vis, newly[k] = S.set_hidden_many(project, sid, k, keys, True)
    hub.emit({"type": "visibility", "project": project, "sid": sid, "visibility": vis})
    return {"visibility": vis, "hidden": newly}


# Capture area

def _capture_info() -> dict:
    cfg = S._read_json(CAPTURE_CONFIG, {"mode": "full"})
    return {"config": cfg, "preview": f"/api/capture/preview?t={CAPTURE_PREVIEW.stat().st_mtime}"
            if CAPTURE_PREVIEW.exists() else None}


async def _refresh_preview():
    await run_helper("--capture", str(CAPTURE_CONFIG), str(CAPTURE_PREVIEW), timeout=20)


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
    rc, out, err = await run_helper(flag, timeout=100)
    line = next((l for l in out.splitlines() if l.startswith(("REGION:", "WINDOW:"))), None)
    if not line:
        return JSONResponse({"cancelled": True, **_capture_info()})
    kind, payload = line.split(":", 1)
    obj = json.loads(payload)
    cfg = {"mode": "region", "rect": obj["rect"]} if kind == "REGION" else {"mode": "window", **obj}
    S._write_json(CAPTURE_CONFIG, cfg)
    await _refresh_preview()
    return _capture_info()


@app.post("/api/capture/detect-video")
async def capture_detect_video():
    """Watch the capture area for a few seconds, find the playing video in it and crop auto
    frames to it (frames_crop in the capture config). Screenshots keep the whole area."""
    import cv2
    import video_detect
    if not CAPTURE_CONFIG.exists():
        S._write_json(CAPTURE_CONFIG, {"mode": "full"})
    cfg = S._read_json(CAPTURE_CONFIG, {"mode": "full"})
    cfg.pop("frames_crop", None)
    tmp = S.WEBUI_DIR / "detect"
    tmp.mkdir(parents=True, exist_ok=True)
    raw_cfg = tmp / "config.json"
    S._write_json(raw_cfg, cfg)
    images = []
    try:
        for i in range(12):
            out = tmp / f"f{i:02d}.png"
            rc, _, err = await run_helper("--capture", str(raw_cfg), str(out), timeout=20)
            if rc == 0 and out.exists():
                images.append(cv2.imread(str(out)))
            await asyncio.sleep(0.4)
    finally:
        for f in tmp.glob("f*.png"):
            f.unlink(missing_ok=True)
    images = [im for im in images if im is not None]
    if len(images) < 4:
        _err(RuntimeError("Could not capture the screen (check Screen Recording permission)"))
    res = await asyncio.to_thread(video_detect.detect, images)
    if not res:
        _err(ValueError("No video found: nothing was changing. Play the video and try again."))
    if res.get("error"):
        _err(ValueError(res["error"]))
    cfg["frames_crop"] = {k: res[k] for k in ("x", "y", "w", "h")}
    cfg["frames_crop_method"] = res["method"]
    S._write_json(CAPTURE_CONFIG, cfg)
    await _refresh_preview()
    return _capture_info()


@app.post("/api/capture/crop")
async def capture_crop(data: dict = Body(default={})):
    """Set the auto-frames crop by hand ({"crop": {x, y, w, h}} in fractions of the area),
    or reset it (no crop: auto frames capture the whole area again)."""
    cfg = S._read_json(CAPTURE_CONFIG, {"mode": "full"})
    crop = data.get("crop")
    if crop:
        try:
            x, y, w, h = (float(crop[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            _err(ValueError("Bad crop"))
        x, y = min(max(x, 0.0), 0.98), min(max(y, 0.0), 0.98)
        w, h = min(max(w, 0.02), 1.0 - x), min(max(h, 0.02), 1.0 - y)
        cfg["frames_crop"] = {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}
        cfg["frames_crop_method"] = "manual"
    else:
        cfg.pop("frames_crop", None)
        cfg.pop("frames_crop_method", None)
    S._write_json(CAPTURE_CONFIG, cfg)
    return _capture_info()


@app.get("/api/capture/windows")
async def capture_windows():
    rc, out, err = await run_helper("--list-windows", timeout=20)
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
    ap = argparse.ArgumentParser(description="ushi web UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="Another host name the UI may be opened by (e.g. this machine's LAN name with --host 0.0.0.0)")
    args = ap.parse_args()
    native_helpers.utf8_stdio()
    ALLOWED_HOSTS.update(h.lower() for h in [args.host, *args.allow_host] if h not in ("0.0.0.0", "::"))
    if args.host in ("0.0.0.0", "::"):
        print("Warning: the UI has no login; anyone on the network who can reach this port controls it.")
    import uvicorn
    url = f"http://{args.host}:{args.port}"
    print(f"ushi web UI: {url}")
    if not args.no_browser:
        import threading
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
