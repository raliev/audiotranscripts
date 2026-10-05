"""Project / session helpers shared by the web UI server and beautify.py.

A *session* is one recording: projects/<project>/transcripts/transcript_<sid>.txt
(sid = YYYYMMDD_HHMMSS). Web-UI artefacts for a session (beautified versions,
chat history, events) live in projects/<project>/sessions/<sid>/.
"""

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = ROOT / "projects"
WEBUI_DIR = ROOT / ".webui"

LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s?(.*)$")
SPEAKER_RE = re.compile(r"^\[Speaker (\d+)\]\s?(.*)$", re.S)
SHOT_RE = re.compile(r"^\[screenshot: (.+)\]$")
SEL_RE = re.compile(r"^\[selection: (.*)\]$", re.S)
SHOT_NAME_RE = re.compile(r"screenshot_(\d{8})_(\d{6})")

DEFAULT_SETTINGS = {
    "beautify_engine": "claude",      # claude (Claude Code CLI) | api (Gemini/OpenAI)
    "beautify_model": "opus",
    "chat_model": "sonnet",
    "api_model": "",                  # for the api engine; empty = provider default
    "auto_final_beautify": True,
    "lang": "auto",
    "speakers": "auto",
    "minutes": 180,
    "pause": 1.5,
    "save_audio": False,
    "recorder_python": "",            # empty = same interpreter as server.py
    "last_project": "",
}

DEFAULT_PROJECT_SETTINGS = {
    "context_dir": "",        # folder where Claude Code runs (claude-context MCP lives there)
    "style_reference": "",    # HTML whose style to copy; empty = built-in template
    "notes": "",              # legacy: migrated to notes.md (see load_notes)
}

CORRECTION_RE = re.compile(r'^\s*[-*]\s*"(.+?)"\s*(?:→|->)\s*"(.+?)"')
CORRECTIONS_HEADING = "## Corrections"


def _read_json(path: Path, default):
    try:
        return {**default, **json.loads(path.read_text(encoding="utf-8"))}
    except Exception:
        return dict(default)


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ── Settings ────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    return _read_json(WEBUI_DIR / "settings.json", DEFAULT_SETTINGS)


def save_settings(data: dict) -> dict:
    merged = {**load_settings(), **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
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
    merged = {**load_project_settings(project),
              **{k: v for k, v in data.items() if k in DEFAULT_PROJECT_SETTINGS}}
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


def add_correction(project: str, original: str, fixed: str, comment: str = "") -> str:
    """Append '- "original" → "fixed"' under the Corrections heading."""
    clean = lambda t: " ".join(t.split()).replace('"', "'")
    line = f'- "{clean(original)}" → "{clean(fixed)}"' + (f"  ({clean(comment)})" if comment.strip() else "")
    text = load_notes(project).rstrip("\n")
    lines = text.split("\n") if text else []
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().lower().startswith(CORRECTIONS_HEADING.lower()))
        end = start + 1
        while end < len(lines) and not lines[end].startswith("#"):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, line)
    except StopIteration:
        if lines:
            lines.append("")
        lines += [CORRECTIONS_HEADING + " (speech-to-text: heard → meant)", line]
    text = "\n".join(lines) + "\n"
    save_notes(project, text)
    return text


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
        if sm:
            entry["speaker"] = int(sm.group(1))
            entry["text"] = sm.group(2)
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
    return entries


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
        versions.append(v)
    return {"versions": versions}
