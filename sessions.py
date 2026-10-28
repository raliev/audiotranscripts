"""Project / session helpers shared by the web UI server and beautify.py.

A *session* is one recording: projects/<project>/transcripts/transcript_<sid>.txt
(sid = YYYYMMDD_HHMMSS). Web-UI artefacts for a session (beautified versions,
chat history, events) live in projects/<project>/sessions/<sid>/.
"""

import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Where projects/ and .webui/ live: the code folder by default; the macOS app
# (app/main.js) points it at a writable folder outside the read-only bundle.
DATA_ROOT = Path(os.environ.get("VOICE_REC_DATA") or ROOT).expanduser().resolve()
PROJECTS_DIR = DATA_ROOT / "projects"
WEBUI_DIR = DATA_ROOT / ".webui"

LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s?(.*)$")
SPEAKER_RE = re.compile(r"^\[Speaker (\d+)\]\s?(.*)$", re.S)
CHANNEL_RE = re.compile(r"^\[(Me|Them)\]\s?(.*)$", re.S)   # app+mic recordings: mic / the app
SHOT_RE = re.compile(r"^\[screenshot: (.+)\]$")
SEL_RE = re.compile(r"^\[selection: (.*)\]$", re.S)
SHOT_NAME_RE = re.compile(r"(?:screenshot|frame)_(\d{8})_(\d{6})")

DEFAULT_SETTINGS = {
    "beautify_engine": "claude",      # claude (Claude Code CLI) | api (Gemini/OpenAI)
    "beautify_model": "opus",
    "chat_model": "sonnet",
    "api_provider": "gemini",         # api engine: gemini | openai | anthropic | openrouter | custom (llm_api.py)
    "api_model": "",                  # for the api engine; empty = provider default
    "api_base_url": "",               # empty = the provider's; required for custom
    "api_layout": "model",            # api engine: model (writes the whole document) | app (writes the
                                      # conversation; the app builds head, header and footer - for small models)
    "ai_setup_done": False,           # the first-run AI setup dialog was answered
    "auto_final_beautify": True,      # offer (ask) to build the final version when recording stops
    "engine": "whisper",              # ASR engine: whisper | gigaam | parakeet
    "lang": "auto",
    "speakers": "auto",
    "minutes": 180,
    "pause": 1.5,
    "save_audio": False,
    "auto_frames": False,             # automatic screen frames (autoframes.py) from the start
    "source": "mic",                  # what to transcribe: mic | app+mic | app | file (main.py --source)
    "source_app": "",                 # bundle id of the app for app / app+mic
    "frames_l1": True,                # auto frames Level 1 (frames_l1.py, CLIP); off = Level 0 only
    "recorder_python": "",            # empty = same interpreter as server.py
    "device": "auto",                 # Whisper / speaker model: auto (cuda when available) | cpu | cuda
    "last_project": "",
    "batch_parallel": 3,              # batch queue: YouTube downloads at once
    "vision_provider": "",            # Describe (image_notes.py): openai | gemini | anthropic | openrouter; empty = auto
    "vision_model": "",               # empty = the provider's default vision model
}

DEFAULT_PROJECT_SETTINGS = {
    "context_dir": "",        # folder where Claude Code runs (claude-context MCP lives there)
    "style_reference": "",    # HTML whose style to copy; empty = built-in template
    "notes": "",              # legacy: migrated to notes.md (see load_notes)
}

CORRECTION_RE = re.compile(r'^\s*[-*]\s*"(.+?)"\s*(?:→|->)\s*"(.+?)"')
CORRECTIONS_HEADING = "## Corrections"
SPEAKERS_HEADING = "## Who said it"
SPEAKER_NOTE_RE = re.compile(r'^\s*(?:[-*]\s*)?".+?"\s*<--\s*(.+?)(?:\s{2,}\(at [\d:]+\))?\s*$')


def _read_json(path: Path, default):
    try:
        return {**default, **json.loads(path.read_text(encoding="utf-8"))}
    except Exception:
        return dict(default)


def _write_json(path: Path, data, mode: int | None = None):
    """Atomic write. mode (e.g. 0o600 for secrets) is set when the file is created, so the
    content is never readable by others, not even for a moment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if mode is None:
        tmp.write_text(text, encoding="utf-8")
    else:
        tmp.unlink(missing_ok=True)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "w", encoding="utf-8") as f:
            f.write(text)
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except PermissionError:      # Windows: the target is open in another thread/process for a moment
            if attempt == 4:
                raise
            time.sleep(0.05)


# ── Settings ────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    return _read_json(WEBUI_DIR / "settings.json", DEFAULT_SETTINGS)


def _check_setting(key: str, value):
    """Values come from the browser: keep each one the type of its default, and only
    accept a Python interpreter as the recorder's (server.py runs it)."""
    default = DEFAULT_SETTINGS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, (int, float)):
        try:
            return type(default)(float(value))
        except (TypeError, ValueError):
            raise ValueError(f"{key}: expected a number")
    value = "" if value is None else str(value).strip()
    if key == "recorder_python" and value:
        exe = Path(value).expanduser()
        if not exe.is_file() or not exe.name.lower().startswith("python"):
            raise ValueError("Python: expected the path of a python executable")
    if key == "device" and value not in ("auto", "cpu", "cuda"):
        raise ValueError("device: auto, cpu or cuda")
    if key == "api_layout" and value not in ("model", "app"):
        raise ValueError("api_layout: model or app")
    return value


def save_settings(data: dict) -> dict:
    merged = {**load_settings(), **{k: _check_setting(k, v) for k, v in data.items() if k in DEFAULT_SETTINGS}}
    _write_json(WEBUI_DIR / "settings.json", merged)
    return merged


def safe_name(name: str) -> str:
    name = (name or "").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9._ -]+", name) or name.startswith("."):
        raise ValueError(f"Invalid name: {name!r}")
    return name


def project_dir(project: str) -> Path:
    return PROJECTS_DIR / safe_name(project)


def load_project_settings(project: str) -> dict:
    return _read_json(project_dir(project) / "project.json", DEFAULT_PROJECT_SETTINGS)


def save_project_settings(project: str, data: dict) -> dict:
    data = {k: str(v or "").strip() for k, v in data.items() if k in DEFAULT_PROJECT_SETTINGS}
    style = data.get("style_reference")
    if style and Path(style).expanduser().suffix.lower() not in (".html", ".htm"):
        raise ValueError("Style reference: expected an .html file")   # it is sent to the LLM in full
    merged = {**load_project_settings(project), **data}
    _write_json(project_dir(project) / "project.json", merged)
    return merged


# ── Notes (one file per project, sent to every LLM request) ────────────────

def notes_path(project: str) -> Path:
    return project_dir(project) / "notes.md"


def load_notes(project: str) -> str:
    path = notes_path(project)
    if path.exists():
        return path.read_text(encoding="utf-8")
    legacy = load_project_settings(project).get("notes", "").strip()
    if legacy:  # migrate the old settings field so notes live in one place
        save_notes(project, legacy + "\n")
        save_project_settings(project, {"notes": ""})
        return legacy + "\n"
    return ""


def save_notes(project: str, text: str):
    path = notes_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _clean(t: str) -> str:
    return " ".join(t.split()).replace('"', "'")


def _append_under(project: str, heading: str, heading_line: str, line: str) -> str:
    """Append a line at the end of the '## heading' section of the notes (creating the section)."""
    text = load_notes(project).rstrip("\n")
    lines = text.split("\n") if text else []
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().lower().startswith(heading.lower()))
        end = start + 1
        while end < len(lines) and not lines[end].startswith("#"):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, line)
    except StopIteration:
        if lines:
            lines.append("")
        lines += [heading_line, line]
    text = "\n".join(lines) + "\n"
    save_notes(project, text)
    return text


def add_correction(project: str, original: str, fixed: str, comment: str = "") -> str:
    """Append '- "original" → "fixed"' under the Corrections heading."""
    line = f'- "{_clean(original)}" → "{_clean(fixed)}"' + (f"  ({_clean(comment)})" if comment.strip() else "")
    return _append_under(project, CORRECTIONS_HEADING, CORRECTIONS_HEADING + " (speech-to-text: heard → meant)", line)


def add_speaker(project: str, quote: str, speaker: str, time: str = "") -> str:
    """Append '- "quote" <-- Speaker' under the Speakers heading."""
    line = f'- "{_clean(quote)}" <-- {_clean(speaker)}' + (f"  (at {time})" if time else "")
    return _append_under(project, SPEAKERS_HEADING, SPEAKERS_HEADING + " (quote <-- who said it)", line)


def known_speakers(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1).strip() for m in (SPEAKER_NOTE_RE.match(l) for l in text.splitlines()) if m))


def parse_corrections(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in (CORRECTION_RE.match(l) for l in text.splitlines()) if m]


# ── Projects & sessions ─────────────────────────────────────────────────────

def list_projects() -> list[dict]:
    out = []
    if not PROJECTS_DIR.exists():
        return out
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        transcripts = [f for f in (d / "transcripts").glob("transcript_*.txt")
                       if not f.name.endswith(".raw.txt")] if (d / "transcripts").exists() else []
        mtime = max([f.stat().st_mtime for f in transcripts] + [d.stat().st_mtime])
        out.append({"name": d.name, "sessions": len(transcripts), "mtime": mtime})
    out.sort(key=lambda p: -p["mtime"])
    return out


def transcript_path(project: str, sid: str) -> Path:
    return project_dir(project) / "transcripts" / f"transcript_{safe_name(sid)}.txt"


def session_dir(project: str, sid: str) -> Path:
    return project_dir(project) / "sessions" / safe_name(sid)


def sid_datetime(sid: str) -> datetime | None:
    try:
        return datetime.strptime(sid, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def list_sessions(project: str) -> list[dict]:
    tdir = project_dir(project) / "transcripts"
    out = []
    if not tdir.exists():
        return out
    for f in tdir.glob("transcript_*.txt"):
        if f.name.endswith(".raw.txt"):
            continue
        sid = f.stem[len("transcript_"):]
        dt = sid_datetime(sid)
        text = f.read_text(encoding="utf-8", errors="replace")
        times = [m.group(1) for m in (LINE_RE.match(l) for l in text.splitlines()) if m]
        manifest = load_manifest(project, sid)
        latest = latest_version(manifest)
        out.append({
            "sid": sid,
            "date": dt.strftime("%Y-%m-%d") if dt else "",
            "start": times[0] if times else (dt.strftime("%H:%M:%S") if dt else ""),
            "end": times[-1] if times else "",
            "lines": len(times),
            "size": f.stat().st_size,
            "diarized": f.with_suffix(".raw.txt").exists(),
            "versions": len([v for v in manifest["versions"] if v.get("status") == "done"]),
            "final": bool(latest and latest.get("mode") == "final"),
            "mtime": f.stat().st_mtime,
        })
    out.sort(key=lambda s: s["sid"], reverse=True)
    return out


# ── Transcript parsing ──────────────────────────────────────────────────────

def parse_transcript(text: str) -> list[dict]:
    """Parse transcript lines into entries. Unprefixed lines (multi-line
    selections) are appended to the previous entry."""
    entries: list[dict] = []
    for raw in text.splitlines():
        m = LINE_RE.match(raw)
        if not m:
            if entries and raw.strip():
                entries[-1]["text"] += "\n" + raw
            continue
        time, body = m.group(1), m.group(2).rstrip()
        entry = {"time": time, "kind": "speech", "speaker": None, "text": body}
        sm = SPEAKER_RE.match(body)
        cm = CHANNEL_RE.match(body)
        if sm:
            entry["speaker"] = int(sm.group(1))
            entry["text"] = sm.group(2)
        elif cm:
            entry["speaker"] = cm.group(1)
            entry["text"] = cm.group(2)
        elif SHOT_RE.match(body):
            entry["kind"] = "screenshot"
            entry["path"] = SHOT_RE.match(body).group(1)
            entry["text"] = ""
        elif body.startswith("[selection: "):
            entry["kind"] = "selection"
            entry["text"] = body[len("[selection: "):]
        entries.append(entry)
    for e in entries:
        if e["kind"] == "selection" and e["text"].endswith("]"):
            e["text"] = e["text"][:-1]
        e["key"] = line_key(e)
    return entries


def line_key(e: dict) -> str:
    """Stable id of a transcript line for hiding: time + text hash (screenshots: file name)."""
    if e.get("kind") == "screenshot":
        return Path(e.get("path", "")).name
    return f'{e["time"]}|{hashlib.sha1(e.get("text", "").strip().encode("utf-8")).hexdigest()[:10]}'


def shot_time(path: str) -> str:
    m = SHOT_NAME_RE.search(Path(path).name)
    if not m:
        return ""
    t = m.group(2)
    return f"{t[:2]}:{t[2:4]}:{t[4:6]}"


def file_url(path: str | Path) -> str | None:
    try:
        rel = Path(path).resolve().relative_to(PROJECTS_DIR.resolve())
    except ValueError:
        return None
    return "/files/" + rel.as_posix()


def screenshot_info(path: str, manifest: dict | None = None) -> dict:
    p = Path(path)
    txt = p.with_suffix(".txt")
    info = {
        "path": str(p),
        "name": p.name,
        "time": shot_time(path),
        "url": file_url(p),
        "exists": p.exists(),
        "captured": True,
        "logged": True,
        "ocr": txt.exists(),
        "ocr_chars": txt.stat().st_size if txt.exists() else 0,
        "described": p.with_suffix(".desc.md").exists(),
        "beautified": None,     # version number that included it
        "in_doc": False,        # referenced by that version's HTML
    }
    if manifest:
        for v in manifest["versions"]:
            if v.get("status") == "done" and p.name in v.get("screenshots", []):
                info["beautified"] = v["v"]
                info["in_doc"] = p.name in v.get("referenced", [])
    return info


def drop_deleted_screenshots(text: str) -> str:
    """Remove [screenshot: …] lines whose image was deleted (moved to .trash)."""
    out = []
    for line in text.splitlines(keepends=True):
        m = LINE_RE.match(line.rstrip("\n"))
        if m:
            sm = SHOT_RE.match(m.group(2).rstrip())
            if sm and not Path(sm.group(1)).exists():
                continue
        out.append(line)
    return "".join(out)


def read_transcript(project: str, sid: str) -> str:
    path = transcript_path(project, sid)
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return drop_deleted_screenshots(text)


def delete_screenshot(project: str, path: str) -> Path:
    """Move a screenshot (and its OCR text) into screenshots/.trash/."""
    shots = (project_dir(project) / "screenshots").resolve()
    p = Path(path).resolve()
    if p.parent != shots or not p.name.startswith("screenshot_") or p.suffix != ".png":
        raise ValueError("Not a screenshot of this project")
    trash = shots / ".trash"
    trash.mkdir(exist_ok=True)
    for f in (p, p.with_suffix(".txt")):
        if f.exists():
            f.replace(trash / f.name)
    return trash / p.name


# ── Auto frames + visibility ────────────────────────────────────────────────
# Lines, screenshots and auto frames can be hidden ("not needed") in the UI. The transcript
# itself is never changed: hidden items live in sessions/<sid>/visibility.json and are left
# out of what Beautify gets. Visible auto frames are added to it as screenshots.

def frames_dir(project: str, sid: str) -> Path:
    return project_dir(project) / "frames" / safe_name(sid)


def load_scenes(project: str, sid: str) -> list[dict]:
    p = frames_dir(project, sid) / "scenes.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))["scenes"]
    except Exception:
        return []


def visibility_path(project: str, sid: str) -> Path:
    return session_dir(project, sid) / "visibility.json"


def load_visibility(project: str, sid: str) -> dict:
    v = _read_json(visibility_path(project, sid), {"lines": [], "shots": [], "frames": []})
    return {"lines": list(v.get("lines", [])), "shots": list(v.get("shots", [])),
            "frames": [int(x) for x in v.get("frames", [])]}


def save_visibility(project: str, sid: str, vis: dict) -> dict:
    vis = {"lines": sorted(set(vis["lines"])), "shots": sorted(set(vis["shots"])),
           "frames": sorted({int(x) for x in vis["frames"]})}
    _write_json(visibility_path(project, sid), vis)
    return vis


def set_hidden(project: str, sid: str, kind: str, key, hidden: bool) -> dict:
    field = {"line": "lines", "shot": "shots", "frame": "frames"}[kind]
    vis = load_visibility(project, sid)
    key = int(key) if kind == "frame" else str(key)
    items = set(vis[field])
    if hidden:
        items.add(key)
    else:
        items.discard(key)
    vis[field] = list(items)
    return save_visibility(project, sid, vis)


def set_hidden_many(project: str, sid: str, kind: str, keys: list, hidden: bool) -> tuple[dict, list]:
    """Hide/show several items; returns (visibility, keys whose state actually changed)."""
    field = {"line": "lines", "shot": "shots", "frame": "frames"}[kind]
    vis = load_visibility(project, sid)
    items = set(vis[field])
    keys = [int(k) if kind == "frame" else str(k) for k in keys]
    changed = [k for k in keys if (k in items) != hidden]
    items = items | set(keys) if hidden else items - set(keys)
    vis[field] = list(items)
    return save_visibility(project, sid, vis), changed


def frame_groups(scenes: list[dict]) -> list[dict]:
    """One entry per distinct image: revisits (same_as) are folded into their original."""
    groups = {s["id"]: {**s, "shows": [[s["wall_start"], s["wall_end"]]]} for s in scenes if not s.get("same_as")}
    for s in scenes:
        g = groups.get(s.get("same_as"))
        if g:
            g["shows"].append([s["wall_start"], s["wall_end"]])
    return list(groups.values())


def visible_frames(project: str, sid: str) -> list[dict]:
    """Auto frames not hidden in the UI, in time order: {id, path, time, title, first_path}
    (path = the state shown last)."""
    hidden = set(load_visibility(project, sid)["frames"])
    out = []
    for g in sorted(frame_groups(load_scenes(project, sid)), key=lambda g: g["wall_start"]):
        path = g.get("last_path") or g["path"]
        if g["id"] in hidden or not Path(path).exists():
            continue
        out.append({"id": g["id"], "path": path, "time": g["wall_start"][11:19],
                    "title": g.get("title") or g.get("caption") or "", "first_path": g["path"]})
    return out


def beautify_text(project: str, sid: str, frames: bool = True) -> str:
    """The transcript as Beautify should see it: hidden lines and screenshots removed,
    visible auto frames (unless frames=False) inserted as "[HH:MM:SS] [screenshot: path]" at their time."""
    vis = load_visibility(project, sid)
    hidden_lines, hidden_shots = set(vis["lines"]), set(vis["shots"])
    blocks: list[tuple[str, str]] = []   # (time, text incl. continuation lines)
    for raw in read_transcript(project, sid).splitlines():
        m = LINE_RE.match(raw)
        if m:
            blocks.append((m.group(1), raw))
        elif blocks:
            blocks[-1] = (blocks[-1][0], blocks[-1][1] + "\n" + raw)
    kept = []
    for t, text in blocks:
        e = parse_transcript(text)
        e = e[0] if e else None
        if e and (e["key"] in hidden_lines or (e["kind"] == "screenshot" and e["key"] in hidden_shots)):
            continue
        kept.append((t, text))
    for f in reversed(visible_frames(project, sid) if frames else []):
        t, path = f["time"], f["path"]
        pos = next((i for i, (bt, _) in enumerate(kept) if bt > t), len(kept))
        kept.insert(pos, (t, f"[{t}] [screenshot: {path}]"))
    return "".join(text + "\n" for _, text in kept)


def load_session(project: str, sid: str) -> dict:
    path = transcript_path(project, sid)
    entries = parse_transcript(read_transcript(project, sid))
    manifest = load_manifest(project, sid)
    shots = [screenshot_info(e["path"], manifest) for e in entries if e["kind"] == "screenshot"]
    return {
        "project": project,
        "sid": sid,
        "transcript_path": str(path),
        "entries": entries,
        "screenshots": shots,
        "diarized": path.with_suffix(".raw.txt").exists(),
        "manifest": public_manifest(project, sid, manifest),
        "visibility": load_visibility(project, sid),
    }


# ── Beautify manifest ───────────────────────────────────────────────────────

def load_manifest(project: str, sid: str) -> dict:
    return _read_json(session_dir(project, sid) / "manifest.json", {"versions": []})


def save_manifest(project: str, sid: str, manifest: dict):
    _write_json(session_dir(project, sid) / "manifest.json", manifest)


def latest_version(manifest: dict) -> dict | None:
    done = [v for v in manifest["versions"] if v.get("status") == "done"]
    return done[-1] if done else None


def public_manifest(project: str, sid: str, manifest: dict) -> dict:
    versions = []
    for v in manifest["versions"]:
        v = dict(v)
        if v.get("html"):
            v["url"] = file_url(session_dir(project, sid) / v["html"])
        if v.get("docs_copy"):
            v["docs_url"] = file_url(v["docs_copy"])
        for k in ("log", "prompt_file"):
            if v.get(k) and (session_dir(project, sid) / v[k]).exists():
                v[k + "_url"] = file_url(session_dir(project, sid) / v[k])
        v["frame_count"] = len(v.pop("frames", None) or [])   # the list is for insert_frames only
        versions.append(v)
    return {"versions": versions}
