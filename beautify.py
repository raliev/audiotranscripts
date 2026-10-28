#!/usr/bin/env python3
"""Beautify: turn a raw transcript (+ screenshots) into a FULL annotated HTML
transcript - every utterance kept, real speakers identified, SUMMARY callouts per topic.
An update run compares the new part with what is already known instead (NEW / CONFIRMS /
UPDATES callouts, via claude-context MCP when available).

Each run produces a new version in projects/<project>/sessions/<sid>/vN.html.
Modes:
  update  extend the previous version in place with the new part of the transcript
  full    rebuild from scratch
  final   rebuild after the session ended (diarized transcript); also copied to docs/

Used by server.py; can also be run from the console:
  python beautify.py <project> <sid> [--mode full|update|final] [--model opus]
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import claude_cli
import llm_api
import external
import image_notes
import select_frames
import sessions as S
from sessions import (
    DATA_ROOT, ROOT, beautify_text, frames_dir, latest_version, load_manifest, load_notes, load_project_settings, load_settings, notes_path,
    parse_transcript, project_dir, save_manifest, session_dir, shot_time,
    sid_datetime, transcript_path, visible_frames,
)

# How the API engine is told to use each external transcript (by its "source" in external/index.json)
EXT_KIND = {
    None: "another tool's transcript of the same meeting: reliable speaker names, often wrong terms; "
          "use it to attribute speakers, keep our terms",
    "youtube": "subtitles of this video uploaded by its author, same timeline as ours: usually accurate, "
               "prefer them for names and terms; no speaker names",
    "youtube-auto": "YouTube's auto-generated captions of this video, same timeline as ours: speech recognition "
                    "with different mistakes, compare with ours and recover missed words; no speaker names",
}
DEFAULT_STYLE = ROOT / "templates" / "annotated_transcript_style.html"
TIME_RE = re.compile(r'class="time"[^>]*>(\d{2}:\d{2}:\d{2})')


PROMPT = """\
Create the {mode_title} of a FULL annotated transcript of a recorded session, as one self-contained HTML file.

## Inputs
- Raw speech-to-text transcript (snapshot): {input}
  {n_lines} lines, {t_first}–{t_last}{live_note}
  Line formats: "[HH:MM:SS] text"; "[HH:MM:SS] [Speaker N] text" (automatic diarization: usually heavily over-split, treat speaker numbers only as weak hints); "[HH:MM:SS] [Me] text" / "[HH:MM:SS] [Them] text" (a call recorded as two channels: Me = the person recording, from their microphone - reliable; Them = everyone else, from the call app's audio; with diarization the Them lines become [Speaker N] next to the [Me] ones); "[HH:MM:SS] [screenshot: /abs/path.png]" (the screen was captured at that moment); "[HH:MM:SS] [selection: text]" (text the presenter highlighted on screen).
- Screenshots taken during the session. View them with the Read tool: they show slides, documents, shared screens and often the meeting's participant list, so use them to identify speakers and to fix names, product names and technical terms. OCR text sits next to each image (.txt) if you need it.
{shots}
{frames}- Style reference: {style}
  Copy ONLY its visual style and structure (CSS incl. dark mode, header, "Who's speaking" chips, callout legend, numbered section dividers, speaker turns, callouts, screenshot figures, footer). Never copy its content.
{prev_block}{context_block}{notes_block}{ext_block}{docs_block}
## Requirements
1. Keep EVERY utterance. Do not summarize, shorten, condense or skip anything, including small talk. Merge fragments into clean speaker turns, fix obvious speech-to-text garbles, and keep each speaker's own wording. Keep the original language of the speech (do not translate).
2. Work out the real speakers from content, self-introductions, how people address each other, and the screenshots. Use one consistent name per person. If a voice can't be identified, give it a descriptive label like "Client (ops)". Give each turn the time of its first line.
3. Group the conversation into numbered sections by topic.
4. {callout_rule}
5. Screenshots: place every screenshot near the moment it was taken, as
   <figure class="shot"><img src="RELATIVE_SRC" alt="…" loading="lazy"><figcaption><span class="time">HH:MM:SS</span>what is shown and why it matters</figcaption></figure>
   Use the exact relative src listed for each screenshot above. (Auto frames, if any, are not yours to place: they are added automatically.)
6. Header: title, subtitle, date and time range, "Who's speaking" chips, callout legend (only the callout types the document uses), hint. Footer: provenance (source transcript name, number of lines, time range, version {v}, generated {now}) plus notes on speaker attribution and corrected terms.
   Write ALL the document's own text (title, subtitle, section titles, roles, callouts, legend, hint, footer) in the language of the speech, not in the language of the style reference.
7. Self-contained HTML with inline CSS, light and dark mode as in the reference, and no external resources except the screenshot images. Write the content as static HTML, with no JavaScript rendering.
8. Output file: {out}
{write_rule}
9. Don't create, modify or delete any other files.
10. When finished, reply with one short line only: identified speakers · sections · callouts · screenshots.
"""

# Built from the transcript alone (full / final): nothing to compare with, so no NEW / CONFIRMS / UPDATES.
CALLOUTS_SUMMARY = """Callouts: after each meaningful topic, add a SUMMARY callout exactly as in the style reference:
   <aside class="callout summary"><div class="top"><span class="badge summary">📝 SUMMARY</span><span class="lead">One-sentence takeaway.</span></div><p>Key points, decisions, numbers and action items, with <b>key terms</b> in bold.</p></aside>
   This document is built from the transcript alone, so do NOT compare it with anything: no NEW / CONFIRMS / UPDATES callouts, no "Prior view" lines, no remarks like "not in any prior document" - even when a previous version, earlier documents or a knowledge base are listed above (use those only for names, terms and context). If the style reference has no summary callout, add a neutral one in the same style (light and dark)."""

# Update: the new part is compared with the part already in the document and with prior sources.
CALLOUTS_COMPARE = """Callouts: after each meaningful topic of the NEW part, add <aside class="callout new|confirm|update"> blocks (🆕 NEW / ✅ CONFIRMS / 🔄 UPDATES) exactly as in the style reference, with a "Prior view:" line saying what we knew before and where it came from (the earlier part of this document, or a prior source). {callout_source}
   Keep the callouts already in the document (e.g. SUMMARY) as they are. If the file has no styles or legend entries for NEW / CONFIRMS / UPDATES yet, add them from the style reference."""

WRITE_RULE_FULL = """   Create it with the Write tool. For a long transcript, write the header plus the first sections first, then append the remaining sections in order with Edit (insert before the closing footer). Keep going until the file covers the whole transcript up to {t_last}."""

WRITE_RULE_UPDATE = """   The file ALREADY contains version {pv} of this document, covering the transcript up to {prev_t_last}. Update it IN PLACE with Edit; don't rewrite it from scratch:
   - Append everything said after {prev_t_last} (input lines {from_line}–{n_lines}) as new turns, sections, callouts and screenshot figures, before the footer.
   - Re-check the existing part only where the new material clarifies something (a speaker's identity, a name, a term, new screenshots), and fix those spots.
   - Update the header (time range, Who's speaking) and the footer (version, line count, time range)."""


FRAMES_BLOCK = """- Auto frames: {n} frames captured from the screen during the session. They are inserted into the document AUTOMATICALLY after you finish, each at its own time: don't add them yourself, and don't Read them all (that would take very long).
  To check names and terms on screen, you may Read a few of these key ones (at most {peek}), the ones the speaker pointed at:
{key}
"""
FRAME_PEEK = 8       # key frames the model may look at; the rest only go into the document

# The prompts as the Edit prompts page shows them. A project can override any of them with
# projects/<project>/prompts/beautify/<name>.txt. {name} is a placeholder, other braces stay as they are.
TEMPLATES = {
    "main": {
        "title": "Main prompt", "default": PROMPT,
        "help": "The whole request sent to the model on every run. The blocks below are inserted into it.",
        "placeholders": {
            "mode_title": "\"current version\" / \"updated version\" / \"FINAL version\"",
            "input": "path of the transcript snapshot", "n_lines": "number of transcript lines",
            "t_first": "time of the first line", "t_last": "time of the last line",
            "live_note": "a note when the recording is still running / has ended",
            "shots": "list of screenshots taken by hand (time, path, src)",
            "frames": "the auto frames note (with a few key frames the model may look at); empty without frames",
            "style": "path of the style reference HTML",
            "prev_block": "the previous version, for consistency (full / final rebuilds)",
            "context_block": "the project knowledge base (claude-context), if set",
            "notes_block": "the project's Notes, if any",
            "ext_block": "external transcripts (Teams, YouTube subtitles), if any",
            "docs_block": "earlier documents in the project's docs/ folder",
            "callout_rule": "one of the two callout blocks below (summary or compare)",
            "v": "version number", "now": "current date and time", "out": "path of the output HTML",
            "write_rule": "one of the two write blocks below (full or update)",
        },
    },
    "callouts_summary": {
        "title": "Callouts: full / final", "default": CALLOUTS_SUMMARY,
        "help": "{callout_rule} on a full rebuild and the final version.", "placeholders": {},
    },
    "callouts_compare": {
        "title": "Callouts: update", "default": CALLOUTS_COMPARE,
        "help": "{callout_rule} when updating the previous version with new material.",
        "placeholders": {"callout_source": "how to back up NEW / CONFIRMS / UPDATES (knowledge base or not)"},
    },
    "write_full": {
        "title": "Writing: full / final", "default": WRITE_RULE_FULL,
        "help": "{write_rule} when the document is written from scratch.",
        "placeholders": {"t_last": "time of the last transcript line"},
    },
    "write_update": {
        "title": "Writing: update", "default": WRITE_RULE_UPDATE,
        "help": "{write_rule} when the previous version is extended in place.",
        "placeholders": {"pv": "previous version number", "prev_t_last": "where the previous version ended",
                         "from_line": "first new input line", "n_lines": "number of input lines"},
    },
}
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def fill(template: str, **values) -> str:
    """Put values into {name} placeholders; unknown names and other braces are left alone."""
    return PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def prompts_dir(project: str) -> Path:
    return project_dir(project) / "prompts" / "beautify"


def load_prompts(project: str) -> dict[str, str]:
    d = prompts_dir(project)
    return {name: (d / f"{name}.txt").read_text(encoding="utf-8") if (d / f"{name}.txt").is_file() else t["default"]
            for name, t in TEMPLATES.items()}


def save_prompt(project: str, name: str, text: str | None) -> None:
    """Save the project's own version of a prompt; None (or the default text) goes back to the default."""
    if name not in TEMPLATES:
        raise ValueError(f"Unknown prompt: {name}")
    p = prompts_dir(project) / f"{name}.txt"
    if text is None or text.strip() == TEMPLATES[name]["default"].strip():
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def prompts_payload(project: str) -> list[dict]:
    texts = load_prompts(project)
    out = []
    for name, t in TEMPLATES.items():
        used = set(PLACEHOLDER_RE.findall(texts[name]))
        out.append({"name": name, "title": t["title"], "help": t["help"], "default": t["default"],
                    "text": texts[name], "custom": (prompts_dir(project) / f"{name}.txt").is_file(),
                    "placeholders": t["placeholders"],
                    "missing": [k for k in t["placeholders"] if k not in used and f"{{{k}}}" in t["default"]]})
    return out


def _rel(path: Path, start: Path) -> str:
    return os.path.relpath(path, start).replace(os.sep, "/")


def _fmt_shots(shots: list[dict], out_dir: Path) -> str:
    if not shots:
        return "  (no screenshots in this session yet)"
    lines = []
    for s in shots:
        p = Path(s["path"])
        ocr = p.with_suffix(".txt")
        lines.append(f"  - {s['time']}  {p}  → src=\"{_rel(p, out_dir)}\""
                     + (f"  (OCR: {ocr.name})" if ocr.exists() else ""))
    return "\n".join(lines)


def prepare(project: str, sid: str, mode: str = "update", live: bool = False) -> dict:
    """Snapshot the transcript, allocate the next version and build the prompt."""
    settings = load_settings()
    psettings = load_project_settings(project)
    src = transcript_path(project, sid)
    if not src.exists():
        raise FileNotFoundError(f"Transcript not found: {src}")

    sdir = session_dir(project, sid)
    sdir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(project, sid)
    prev = latest_version(manifest)
    if mode == "update" and (not prev or not (sdir / prev["html"]).exists()):
        mode = "full"

    v = max([x["v"] for x in manifest["versions"]] + [0]) + 1
    snapshot = sdir / f"input_v{v}.txt"
    # Without the lines/screenshots hidden in the UI. Auto frames are not in it: there can be
    # hundreds, so they go into the document afterwards (insert_frames), not through the model.
    text = split_long_lines(beautify_text(project, sid, frames=False), project, sid)
    snapshot.write_text(text, encoding="utf-8")
    entries = parse_transcript(text)
    raw_lines = text.splitlines()
    timed = [l for l in raw_lines if l.startswith("[")]
    t_first = entries[0]["time"] if entries else "-"
    t_last = entries[-1]["time"] if entries else "-"
    shots = [{"path": e["path"], "time": shot_time(e["path"]) or e["time"]}
             for e in entries if e["kind"] == "screenshot" and Path(e["path"]).exists()]
    frames = visible_frames(project, sid)
    frames_block = ""
    if frames:
        try:
            ids = {f["id"] for f in frames}
            top = [r["id"] for r in select_frames.score_scenes(project, sid) if r["id"] in ids][:FRAME_PEEK]
        except Exception:
            top = []
        key = [f for f in frames if f["id"] in top]
        frames_block = fill(FRAMES_BLOCK, n=len(frames), peek=FRAME_PEEK,
                            key="\n".join(f"    {f['time']}  {f['path']}" for f in key) or "    (none)")
    P = load_prompts(project)

    out = sdir / f"v{v}.html"
    style = Path(psettings["style_reference"]).expanduser() if psettings["style_reference"] else DEFAULT_STYLE
    if not style.exists():
        style = DEFAULT_STYLE

    prev_block = ""
    write_rule = fill(P["write_full"], t_last=t_last)
    from_line = 1
    if mode == "update":
        shutil.copyfile(sdir / prev["html"], out)
        prev_t_last = prev.get("t_last", "")
        for i, l in enumerate(raw_lines, 1):
            m = re.match(r"^\[(\d{2}:\d{2}:\d{2})\]", l)
            if m and m.group(1) > prev_t_last:
                from_line = i
                break
        else:
            from_line = len(raw_lines)
        write_rule = fill(P["write_update"], pv=prev["v"], prev_t_last=prev_t_last,
                          from_line=from_line, n_lines=len(raw_lines))
    elif prev and (sdir / prev["html"]).exists():
        prev_block = (f"- Previous version (v{prev['v']}, {prev.get('mode')}): {sdir / prev['html']}\n"
                      "  Use it only for consistency (speaker names, section names, terms); don't compare "
                      "with it. The transcript is the source of truth.\n")

    context_dir = psettings["context_dir"].strip()
    if context_dir and mode != "update":
        context_block = (
            f"- Project knowledge base: {context_dir}\n"
            "  If the claude-context MCP server is available, search it (search_code with this path) for "
            "names, terms and background: earlier documents, transcripts, notes.\n")
    elif context_dir:
        context_block = (
            f"- Project knowledge base: {context_dir}\n"
            "  If the claude-context MCP server is available, search it (search_code with this path) to check "
            "what is said against what we already know: earlier documents, transcripts, notes.\n")
        callout_source = ("Back up every NEW / CONFIRMS / UPDATES classification with a search of the "
                          "project knowledge base. Never invent prior knowledge.")
    else:
        context_block = ""
        callout_source = ("No knowledge base is configured, so compare against the earlier part of this "
                          "document and earlier documents of this project (if any). Where there is no prior "
                          "source, use NEW for new facts and decisions. Never invent prior knowledge.")
    callout_rule = (fill(P["callouts_compare"], callout_source=callout_source) if mode == "update"
                    else P["callouts_summary"])

    notes = load_notes(project).strip()
    notes_block = (
        f"- The user's notes for this project ({notes_path(project)}). They are AUTHORITATIVE: they cover who we are, "
        "the client, participants and terms. Apply every correction (heard → meant) everywhere in the transcript, "
        "including other spellings and mishearings of the same words. A line '\"quote\" <-- Name' means Name said "
        "that quote: attribute that turn to Name, and use it to recognise Name's voice elsewhere:\n"
        + "".join(f"    {l}\n" for l in notes.splitlines()) if notes else "")

    ext_files = external.for_session(project, sid)
    # YouTube subtitles of the video itself (batch.py) are not a meeting tool's transcript
    yt_src = {(external.ext_dir(project) / f["text"]): f.get("source") for f in external.load_index(project)
              if str(f.get("source", "")).startswith("youtube")}
    yt_files = [p for p in ext_files if p in yt_src]
    ext_files_meeting = [p for p in ext_files if p not in yt_src]
    yt_block = (
        "- Subtitles of this video from YouTube (same timeline as ours: their times are positions in the video):\n"
        + "".join(f"    {p}  ({'auto-generated by YouTube' if yt_src[p] == 'youtube-auto' else 'uploaded by the author'})\n"
                  for p in yt_files)
        + "  Read them in full and use them to correct our transcript:\n"
        "  - Uploaded by the author: usually accurate, so prefer them for names, terms and wording where ours "
        "differs.\n"
        "  - Auto-generated: speech recognition like ours, with different mistakes: compare the two and keep "
        "what makes sense; recover words ours missed or garbled.\n"
        "  - They carry no speaker names: attribute speakers from our transcript, the notes and the content.\n") \
        if yt_files else ""
    ext_block = yt_block + ((
        "- External transcript(s) of the SAME meeting from another tool (e.g. Microsoft Teams):\n"
        + "".join(f"    {p}\n" for p in ext_files_meeting)
        + "  Read them in full and merge them with ours into one better document:\n"
        "  - Speaker names there are reliable (they come from meeting accounts): use them to attribute turns.\n"
        "  - Brand names, product names and technical terms there are often misrecognized; ours (plus the notes "
        "and screenshots) usually has them right, so prefer our wording for those.\n"
        "  - Its timestamps may be relative to the start of that recording; align the two by content, but keep "
        "OUR times in the document.\n"
        "  - Where ours is garbled or missed something that the external one has, recover it from there.\n"
        "  - Say in the footer that an external transcript was used for speaker attribution.\n") \
        if ext_files_meeting else "")

    if mode == "update" and (ext_files or "<--" in notes):
        write_rule += ("\n   - Re-check speaker attribution in the EXISTING part too, against the external transcript and the "
                       "'quote <-- Name' notes, and fix wrong or generic speaker labels (header chips included).")

    docs_dir = project_dir(project) / "docs"
    docs = sorted(p.name for p in docs_dir.glob("*") if p.suffix in (".html", ".txt", ".md")) if docs_dir.exists() else []
    docs_block = (f"- Earlier documents for this project (may help with names and context): {docs_dir}\n"
                  + "".join(f"    {d}\n" for d in docs[-15:])) if docs else ""

    mode_title = {"final": "FINAL version", "full": "current version", "update": "updated version"}[mode]
    live_note = (" (the session is still being recorded, so the transcript ends abruptly; that's expected)"
                 if live and mode != "final" else "")
    if mode == "final":
        live_note += ". The recording has ENDED, so this is the definitive document: identify every speaker."

    prompt = fill(
        P["main"], mode_title=mode_title, input=snapshot, n_lines=len(timed), t_first=t_first, t_last=t_last,
        live_note=live_note, shots=_fmt_shots(shots, sdir), frames=frames_block, style=style, prev_block=prev_block,
        context_block=context_block, notes_block=notes_block, ext_block=ext_block, docs_block=docs_block,
        callout_rule=callout_rule, v=v, now=datetime.now().strftime("%Y-%m-%d %H:%M"),
        out=out, write_rule=write_rule,
    )

    record = {
        "v": v, "mode": mode, "status": "running", "created": datetime.now().isoformat(timespec="seconds"),
        "html": out.name, "input": snapshot.name, "lines": len(timed), "t_first": t_first, "t_last": t_last,
        "screenshots": [Path(s["path"]).name for s in shots] + [Path(f["path"]).name for f in frames],
        "frames": [{"id": f["id"], "path": f["path"], "time": f["time"], "title": f["title"]} for f in frames],
        "custom_prompts": [n for n in TEMPLATES if (prompts_dir(project) / f"{n}.txt").is_file()],
        "log": f"v{v}.log", "prompt_file": f"v{v}.prompt.txt",
        "engine": settings["beautify_engine"],
        "model": llm_api.config(settings)["model"] if settings["beautify_engine"] == "api" else settings["beautify_model"],
    }
    manifest["versions"].append(record)
    save_manifest(project, sid, manifest)
    (sdir / record["prompt_file"]).write_text(prompt, encoding="utf-8")

    cwd = Path(context_dir).expanduser() if context_dir and Path(context_dir).expanduser().is_dir() else DATA_ROOT
    return {
        "project": project, "sid": sid, "v": v, "mode": mode, "out": out, "input": snapshot,
        "prompt": prompt, "cwd": cwd, "style": style, "settings": settings,
        "add_dirs": sorted({str(project_dir(project).resolve()), str(ROOT)}),
        "labels": {str(snapshot): "Reading the transcript", snapshot.name: "Reading the transcript",
                   str(style): "Studying the style reference", style.name: "Studying the style reference",
                   **({str(sdir / prev["html"]): f"Reading previous version v{prev['v']}"} if prev else {}),
                   **{str(p): "Reading the external transcript" for p in ext_files}},
        "log": sdir / record["log"], "frame_count": len(frames),
        "ext_files": ext_files, "yt_sources": {str(p): v for p, v in yt_src.items()}, "shot_count": len(shots), "lines": len(timed), "t_last": t_last,
        "shots": [{**x, "src": _rel(Path(x["path"]), sdir)} for x in shots],
        "image_items": image_notes.index_items(shots, frames),
    }


SCREEN_NOTES_BLOCK = """
## What was on screen
Notes made from the screenshots and auto frames (their descriptions or OCR, boilerplate removed), by time. \
Use them to spell names and terms right and to know what the speakers were looking at; they are not speech.
{notes}
"""


def _ask_claude(prompt: str, model: str) -> str:
    """One text reply from Claude Code, no tools."""
    exe = claude_cli.claude_binary()
    if not exe:
        raise RuntimeError("Claude Code CLI ('claude') not found in PATH")
    r = subprocess.run([exe, "-p", "--output-format", "text", "--model", model or "sonnet", "--tools", ""],
                       input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(DATA_ROOT), timeout=900)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:] or f"claude exit code {r.returncode}")
    return r.stdout


def screen_notes(spec: dict, log: "RunLog | None" = None) -> str:
    """Condense what the session's images show (image_notes.condense) with the Beautify engine.
    Saved as vN.screen.txt and kept in spec["screen_notes"]; the same image texts are not
    condensed twice (screen_notes.json in the session folder). '' when there is nothing or it fails."""
    items = spec.get("image_items") or []
    spec["screen_notes"] = ""
    if not items:
        return ""
    sdir = Path(spec["out"]).parent
    cache_path = sdir / "screen_notes.json"
    key = image_notes.index_key(items)
    cache = S._read_json(cache_path, {})
    write = log.write if log else (lambda _t: None)
    notes = cache.get(key, "")
    if notes:
        write(f"Screen notes: {len(items)} images, unchanged since the last run (reused)")
    else:
        settings = spec["settings"]
        if settings["beautify_engine"] == "api":
            cfg = llm_api.config(settings)
            ask = lambda prompt: llm_api.stream(cfg, [{"role": "user", "content": prompt}])
        else:
            ask = lambda prompt: _ask_claude(prompt, settings.get("beautify_model"))
        started = time.time()
        try:
            notes = image_notes.condense(items, ask, write)
        except Exception as e:
            write(f"Screen notes failed, the raw OCR text is used instead: {llm_api.explain(e)}")
            return ""
        write(f"Screen notes: {len(items)} images → {len(notes):,} chars in {time.time() - started:.0f}s")
        S._write_json(cache_path, {key: notes})
    (sdir / f"v{spec['v']}.screen.txt").write_text(notes, encoding="utf-8")
    spec["screen_notes"] = notes
    if notes and spec["settings"]["beautify_engine"] != "api":
        spec["prompt"] += fill(SCREEN_NOTES_BLOCK, notes=notes)
    return notes


def claude_command(spec: dict) -> list[str]:
    # Writes only into this session's folder. The transcript, notes, OCR text and external
    # transcripts are untrusted input (anyone in the meeting can say "edit server.py"), so
    # no acceptEdits: -p denies every edit that isn't allowed by this rule.
    tools = ["Read", "Glob", "Grep", "TodoWrite", "mcp__claude-context",
             claude_cli.edit_rule(Path(spec["out"]).parent)]
    return claude_cli.build_command(spec["settings"]["beautify_model"], tools, spec["add_dirs"])


def html_stats(html: str, shot_names: list[str]) -> dict:
    times = TIME_RE.findall(html)
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return {
        "title": re.sub(r"\s+", " ", title.group(1)).strip() if title else "",
        "turns": html.count('class="turn"'),
        "callouts": len(re.findall(r'class="callout\b', html)),
        "figures": len(re.findall(r'class="shot\b', html)),
        "referenced": [n for n in shot_names if n in html],
        "doc_t_first": min(times) if times else "",
        "doc_t_last": max(times) if times else "",
        "size": len(html.encode("utf-8")),
    }


# Placeholder text of the default style reference: a weak model fills the reference in
# instead of writing up the transcript.
TEMPLATE_LEFTOVERS = ("Firstname Lastname", "One-sentence takeaway", "Weekday, Month DD", "Session title - ",
                      "[SPEAKER NAME]", "[WHAT WAS SAID]", "[ONE-SENTENCE TAKEAWAY]", "[SECTION TITLE]")
NOT_FROM_TRANSCRIPT = "not made from the transcript"


def copied_template(html: str, t_first: str = "", t_last: str = "") -> str:
    """Why the document looks like the style reference filled in rather than the transcript; '' if it doesn't."""
    left = [s.strip(" -") for s in TEMPLATE_LEFTOVERS if s in html]
    if left:
        return "it copies the style reference's placeholder text (" + ", ".join(f'"{s}"' for s in left) + ")"
    times = TIME_RE.findall(html)
    if times and t_first and t_first <= t_last and not any(t_first <= t <= t_last for t in times):
        return f"none of its times ({min(times)}–{max(times)}) are in the transcript ({t_first}–{t_last})"
    return ""


def _slug(title: str) -> str:
    t = re.split(r"\s[-–-]\s|\(", title)[0] if title else ""
    s = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return "-".join(s.split("-")[:6]) or "session"


def finish(project: str, sid: str, v: int, status: str, **info) -> dict:
    """Record the outcome of a version; on a successful final, copy it to docs/."""
    manifest = load_manifest(project, sid)
    rec = next(x for x in manifest["versions"] if x["v"] == v)
    rec.update(info)
    rec["status"] = status
    rec["finished"] = datetime.now().isoformat(timespec="seconds")
    out = session_dir(project, sid) / rec["html"]
    if status == "done":
        if not out.exists() or out.stat().st_size < 500:
            rec["status"] = "failed"
            rec["error"] = "The model finished without writing the document."
        else:
            html = out.read_text(encoding="utf-8", errors="replace")
            if "</html>" not in html[-3000:].lower() and "no end" not in rec.get("warning", ""):
                rec["warning"] = "; ".join(x for x in (rec.get("warning"), "the document has no end (</html>): it was cut off") if x)
            copied = copied_template(html, rec.get("t_first", ""), rec.get("t_last", ""))
            if copied and NOT_FROM_TRANSCRIPT not in rec.get("warning", ""):
                rec["warning"] = "; ".join(x for x in (rec.get("warning"), f"{NOT_FROM_TRANSCRIPT}: {copied}") if x)
            html, added = insert_frames(html, rec.get("frames", []), out.parent, rec.get("t_last", ""))
            if added or 'id="ushi-frames"' in html:
                out.write_text(html, encoding="utf-8")
            rec["frames_added"] = added
            rec.update(html_stats(html, rec["screenshots"]))
            if rec["mode"] == "final":
                rec["docs_copy"] = str(_copy_to_docs(project, sid, out, rec, manifest))
    save_manifest(project, sid, manifest)
    return rec


ANCHOR_RE = re.compile(r'<div\s+class="(turn|section)\b')
# Our own, so screenshots fit the text column whatever CSS the model wrote (it may write none).
FRAME_STYLE = ('<style id="ushi-frames">figure.shot{max-width:100%;margin:14px 0}'
               'figure.shot img{display:block;max-width:100%;height:auto}'
               'figure.shot.frame{width:640px;max-width:100%;border:1px solid rgba(128,128,128,.28);'
               'border-radius:10px;overflow:hidden}'
               'figure.shot.frame img{width:100%}'
               'figure.shot.frame figcaption{font-size:.8em;opacity:.8;padding:4px 10px;margin:0}</style>')
FRAME_STYLE_RE = re.compile(r'<style id="ushi-frames">.*?</style>', re.S)


def restyle(html: str) -> str:
    """Put the current FRAME_STYLE into a document (replacing an older one)."""
    if FRAME_STYLE_RE.search(html):
        return FRAME_STYLE_RE.sub(lambda m: FRAME_STYLE, html, count=1)
    return html.replace("</head>", FRAME_STYLE + "</head>", 1) if "</head>" in html else FRAME_STYLE + html


def _secs(t: str) -> int:
    h, m, sec = map(int, t.split(":"))
    return h * 3600 + m * 60 + sec


def _break_points(html: str, start: int, stop: int, boxed: list[tuple[int, int]]) -> list[tuple[int, str, str]]:
    """Where a figure can go between start and stop: after a paragraph, or between two sentences
    of one (the paragraph is closed and reopened there). Never inside a callout or a figure,
    never inside inline markup. Returns (position, text before the figure, text after it)."""
    out = []
    for m in re.finditer(r"</p>|(?<=[.!?…])\s+(?=[^\s<])", html[start:stop], re.I):
        pos = start + m.start()
        if any(a <= pos < b for a, b in boxed):
            continue
        if m.group(0).lower() == "</p>":
            out.append((pos + 4, "", ""))
            continue
        last_tag = html.rfind("<", 0, pos)
        tag = re.match(r"<(/?)(\w+)[^>]*>", html[last_tag:pos + 1]) if last_tag >= 0 else None
        if not tag:
            continue
        if not tag.group(1) and tag.group(2).lower() == "p":        # right in a paragraph's own text
            out.append((pos, "</p>", tag.group(0)))
        elif tag.group(1) and tag.group(2).lower() in ("b", "i", "em", "strong", "span", "a", "code"):
            opening = list(re.finditer(r"<p\b[^>]*>|</p>", html[start:pos], re.I))
            if opening and opening[-1].group(0).lower() != "</p>":
                out.append((pos, "</p>", opening[-1].group(0)))
    return out


PIECE_SEC = 30          # a longer transcript line is cut into lines of about this much speech
SPLIT_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\] ((?:\[[^\]:]+\] )?)(\S.*)$")
SENTENCE_RE = re.compile(r"(?<=[.!?…])[»\"')\]]*\s+")


def _hms(secs: float) -> str:
    s = int(secs) % 86400
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def _chunk_times(project: str, sid: str) -> dict[str, tuple[list[tuple[str, float]], float]]:
    """From the session's events: line time → ([(ASR chunk text, its end in seconds from the
    line start)], segment duration). Older recordings wrote minutes of speech as one line;
    this is where the words inside it were actually said."""
    out: dict = {}
    path = session_dir(project, sid) / "events.jsonl"
    if not path.exists():
        return out
    start, cur = None, {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev.get("type") == "session":
            try:
                start = datetime.fromisoformat(ev["start_time"])
            except (KeyError, TypeError, ValueError):
                start = None
        elif ev.get("type") == "transcribe_progress" and start and ev.get("text"):
            t = ev.get("t", 0.0)
            secs = start.hour * 3600 + start.minute * 60 + start.second + t
            key = _hms(secs)
            if cur.get("t") != t:
                cur = {"t": t}
                out[key] = ([], ev.get("duration", 0.0))
            out[key][0].append((ev["text"], ev.get("done", 0.0)))
    return out


def split_long_lines(text: str, project: str = "", sid: str = "") -> str:
    """Cut every transcript line that covers more than 2×PIECE_SEC into lines of about PIECE_SEC,
    at sentence ends, each with the time its first sentence was said: the real time of the
    ASR chunk from the session's events, interpolated by characters inside a chunk (or across
    the whole line when there are no events). Without it a ten-minute line has one time, the
    model gives all its turns that time, and the auto frames of those ten minutes have nowhere
    to go but after it."""
    lines = text.splitlines()
    times = [LINE_TIME_RE.match(l) for l in lines]
    chunks = _chunk_times(project, sid) if project else {}
    out = []
    for i, line in enumerate(lines):
        m = SPLIT_LINE_RE.match(line)
        if not m or m.group(3).startswith(("[screenshot:", "[selection:")):
            out.append(line)
            continue
        t0, tag, body = _secs(m.group(1)), m.group(2), m.group(3)
        nxt = next((_secs(x.group(1)) for x in times[i + 1:] if x and x.group(1) > m.group(1)), None)
        # (characters into the body, seconds) points to interpolate between
        points = [(0, 0.0)]
        if m.group(1) in chunks:
            parts, dur = chunks[m.group(1)]
            total = sum(len(p) + 1 for p, _ in parts) - 1
            scale = len(body) / max(1, total)
            pos = -1
            for p, done in parts:
                pos += len(p) + 1
                points.append((pos * scale, done))
            end = dur
        else:
            end = (nxt - t0) if nxt is not None else 0
        if end < 2 * PIECE_SEC:
            out.append(line)
            continue
        if points[-1][0] < len(body):
            points.append((len(body), end))

        def at(c: float) -> float:
            for (c0, s0), (c1, s1) in zip(points, points[1:]):
                if c <= c1:
                    return s0 + (s1 - s0) * (c - c0) / max(1e-9, c1 - c0)
            return points[-1][1]

        pieces, start_s, buf, c = [], 0.0, [], 0
        sentences = [x for x in SENTENCE_RE.sub(lambda x: x.group(0).rstrip() + "\n", body).split("\n") if x]
        for sent in sentences:
            s = at(c)
            if buf and s - start_s >= PIECE_SEC:
                pieces.append((start_s, " ".join(buf)))
                buf, start_s = [], s
            buf.append(sent)
            c += len(sent) + 1
        pieces.append((start_s, " ".join(buf)))
        out += [f"[{_hms(t0 + s)}] {tag}{p}" for s, p in pieces]
    return "".join(l + "\n" for l in out)


def insert_frames(html: str, frames: list[dict], out_dir: Path, t_end: str = "") -> tuple[str, int]:
    """Put every auto frame the document doesn't have yet into the turn it was on screen during
    (the last turn that started at or before it), at the sentence at the same share of the turn's
    time - one turn can be minutes of speech. Returns (html, frames added)."""
    todo = [f for f in frames if Path(f["path"]).name not in html]
    if not todo:
        return (restyle(html) if "<figure" in html else html), 0
    end = next((i for i in (html.rfind("<footer"), html.rfind("</main>"), html.rfind("</body>")) if i >= 0), len(html))
    anchors: list[tuple[int, str, bool]] = []    # (position, time, is a turn); a section gets its first turn's time
    sections: list[int] = []
    for m in ANCHOR_RE.finditer(html, 0, end):
        if m.group(1) == "section":
            sections.append(m.start())
            continue
        t = TIME_RE.search(html, m.end(), min(m.end() + 1500, end))
        if t:
            # A turn earlier than the one before it is the model's slip (it repeated an old time):
            # it continues the turn before, it doesn't pull later frames back to it.
            tm = max(t.group(1), anchors[-1][1]) if anchors else t.group(1)
            anchors += [(p, tm, False) for p in sections] + [(m.start(), tm, True)]
            sections = []
    boxed = [(m.start(), m.end()) for m in re.finditer(r"<(aside|figure)\b.*?</\1>", html, re.S | re.I)]
    at: dict[int, list] = {}        # position → [text before, figures, text after]
    for f in sorted(todo, key=lambda f: f["time"]):
        i = max((k for k, a in enumerate(anchors) if a[1] <= f["time"]), default=-1)
        pos, before, after = (anchors[i + 1][0] if i + 1 < len(anchors) else end), "", ""
        if i >= 0:
            # Turns in a row with the same time are one stretch (a long transcript line becomes
            # several turns): spread the frames over all of it.
            j = i
            while j > 0 and anchors[j - 1][1] == anchors[i][1]:
                j -= 1
            start, t0 = anchors[j][0], _secs(anchors[i][1])
            t1 = next((_secs(a[1]) for a in anchors[i + 1:] if a[1] > anchors[i][1]),
                      _secs(t_end) if t_end and t_end > anchors[i][1] else t0 + 60)
            points = _break_points(html, start, pos, boxed)
            if points:
                share = min(1.0, (_secs(f["time"]) - t0) / max(1, t1 - t0))
                target = start + share * (pos - start)
                pos, before, after = min(points, key=lambda p: abs(p[0] - target))
        cap = f" {f['title']}" if f.get("title") else ""
        at.setdefault(pos, [before, [], after])[1].append(
            f'<figure class="shot frame" data-frame="{f["id"]}"><img src="{_rel(Path(f["path"]), out_dir)}" '
            f'alt="Screen at {f["time"]}" loading="lazy"><figcaption><span class="time">{f["time"]}</span>'
            f'{_esc(cap)}</figcaption></figure>')
    for pos in sorted(at, reverse=True):
        before, figs, after = at[pos]
        html = html[:pos] + before + "\n" + "\n".join(figs) + "\n" + after + html[pos:].lstrip(" ") if after \
            else html[:pos] + "\n" + "\n".join(figs) + "\n" + html[pos:]
    return restyle(html), len(todo)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _copy_to_docs(project: str, sid: str, out: Path, rec: dict, manifest: dict) -> Path:
    """Save the final document to docs/ as one self-contained file (images embedded as
    data: URIs, so it opens anywhere) plus a light "-text.html" copy without the images."""
    docs = project_dir(project) / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    existing = [x.get("docs_copy") for x in manifest["versions"] if x.get("docs_copy")]
    if existing:
        target = Path(existing[-1])
    else:
        dt = sid_datetime(sid)
        date = dt.strftime("%Y-%m-%d") if dt else sid
        target = docs / f"{date}-{_slug(rec.get('title', ''))}-annotated-transcript.html"
    html = out.read_text(encoding="utf-8")
    target.write_text(_embed_images(html, out.parent, project, sid), encoding="utf-8")
    text_only = re.sub(r'\s*<figure class="shot".*?</figure>', "", html, flags=re.S | re.I)
    target.with_name(target.stem + "-text.html").write_text(text_only, encoding="utf-8")
    return target


IMG_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


def _find_image(src: str, base: Path, project: str, sid: str) -> Path | None:
    """The file an <img src> means. A model sometimes gets the folder wrong (an auto frame
    under screenshots/), so fall back to the file name in screenshots/ and frames/<sid>/."""
    if src.startswith(("data:", "http:", "https:")):
        return None
    p = (base / src).resolve()
    if p.is_file():
        return p
    name = Path(src).name
    for d in (project_dir(project) / "screenshots", frames_dir(project, sid)):
        if (d / name).is_file():
            return d / name
    return next(project_dir(project).glob(f"frames/*/{name}"), None)


def embed_version(project: str, sid: str, v: int) -> Path:
    """sessions/<sid>/vN.html → vN-emb.html next to it, with every image embedded as a data:
    URI: one file that opens anywhere, to share without the screenshots/ and frames/ folders."""
    src = session_dir(project, sid) / f"v{int(v)}.html"
    if not src.is_file():
        raise FileNotFoundError(f"No v{v} in session {sid}")
    out = src.with_name(f"v{int(v)}-emb.html")
    out.write_text(_embed_images(src.read_text(encoding="utf-8"), src.parent, project, sid), encoding="utf-8")
    return out


def _embed_images(html: str, base: Path, project: str, sid: str) -> str:
    def repl(m):
        p = _find_image(m.group(2), base, project, sid)
        mime = p and IMG_TYPES.get(p.suffix.lower())
        if not mime:
            return m.group(0)
        return f'{m.group(1)}data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}{m.group(3)}'
    return re.sub(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', repl, html, flags=re.I)


# ── Run log: where the minutes go ────────────────────────────────────────────

class RunLog:
    """A readable trace of one run in sessions/<sid>/vN.log: each model call with its time
    and tokens, each tool call with how long it took, and the totals at the end."""

    def __init__(self, path: Path):
        self.f = open(path, "w", encoding="utf-8", buffering=1)
        self.t0 = time.time()
        self.calls = 0
        self.call: dict = {}
        self.tools: dict[str, tuple[str, float]] = {}
        self.model_time = self.tool_time = 0.0
        self.out_tokens = 0

    def write(self, text: str):
        if not self.f.closed:
            self.f.write(f"{datetime.now():%H:%M:%S} {time.time() - self.t0:7.1f}s  {text}\n")

    def header(self, spec: dict, engine: str, model: str, command: list[str] | None = None):
        sh = spec["shots"]
        self.write(f"Beautify v{spec['v']} ({spec['mode']}) · {spec['project']} / {spec['sid']}")
        self.write(f"Engine {engine} · model {model or 'default'}")
        self.write(f"Input: {spec['lines']} lines up to {spec['t_last']} · {len(sh)} screenshots for the model · "
                   f"{spec['frame_count']} auto frames (inserted afterwards, not sent)")
        self.write(f"Prompt: {len(spec['prompt']):,} chars, saved as {Path(spec['log']).stem}.prompt.txt")
        if command:
            self.write(f"cwd {spec['cwd']}")
            self.write("Command: " + " ".join(command))

    @staticmethod
    def _tool_args(name: str, inp: dict) -> str:
        if name == "Read":
            extra = "".join(f" {k}={inp[k]}" for k in ("offset", "limit") if inp.get(k))
            return f"{inp.get('file_path', '')}{extra}"
        if name == "Write":
            return f"{inp.get('file_path', '')} ({len(inp.get('content') or '') / 1024:.1f} KB)"
        if name in ("Edit", "MultiEdit"):
            return (f"{Path(inp.get('file_path', '')).name}: {len(inp.get('old_string') or ''):,} → "
                    f"{len(inp.get('new_string') or ''):,} chars")
        if name in ("Grep", "Glob"):
            return f"{inp.get('pattern', '')} {inp.get('path', '')}".strip()
        if name == "TodoWrite":
            return f"{len(inp.get('todos') or [])} items"
        return json.dumps(inp, ensure_ascii=False)[:200]

    def event(self, ev: dict):
        et = ev.get("type")
        now = time.time()
        if et == "system" and ev.get("subtype") == "init":
            servers = ev.get("mcp_servers", [])
            mcp = ", ".join(m.get("name", "") for m in servers if m.get("status") == "connected") or "none connected"
            if len(servers) > sum(m.get("status") == "connected" for m in servers):
                mcp += f" (+{len(servers) - sum(m.get('status') == 'connected' for m in servers)} not connected)"
            self.write(f"Claude Code started · model {ev.get('model')} · {len(ev.get('tools', []))} tools · MCP: {mcp}")
        elif et == "stream_event":
            inner = ev.get("event", {})
            it = inner.get("type")
            if it == "message_start":
                self.calls += 1
                u = inner.get("message", {}).get("usage", {}) or {}
                new, cr, cw = u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0)
                self.call = {"t": now, "first": None, "out": 0, "stop": ""}
                self.write(f"Model call #{self.calls}: context {new + cr + cw:,} tokens "
                           f"(cached {cr:,}, newly cached {cw:,}, uncached {new:,})")
            elif it == "content_block_start" and self.call and self.call["first"] is None:
                self.call["first"] = now
            elif it == "message_delta":
                self.call["out"] = (inner.get("usage") or {}).get("output_tokens", self.call.get("out", 0))
                self.call["stop"] = (inner.get("delta") or {}).get("stop_reason") or ""
            elif it == "message_stop" and self.call:
                dt = now - self.call["t"]
                first = (self.call["first"] or now) - self.call["t"]
                out = self.call["out"]
                self.model_time += dt
                self.out_tokens += out
                gen = dt - first
                self.write(f"Model call #{self.calls} done in {dt:.1f}s · first token after {first:.1f}s · "
                           f"{out:,} tokens out" + (f" ({out / gen:.0f}/s)" if gen > 0.5 else "")
                           + (f" · {self.call['stop']}" if self.call["stop"] else ""))
                self.call = {}
        elif et == "assistant":
            for b in ev.get("message", {}).get("content", []) or []:
                if b.get("type") == "tool_use":
                    self.tools[b.get("id", "")] = (b.get("name", ""), now)
                    self.write(f"  → {b.get('name')} {self._tool_args(b.get('name', ''), b.get('input') or {})}")
                elif b.get("type") == "text" and (b.get("text") or "").strip():
                    self.write("  says: " + re.sub(r"\s+", " ", b["text"]).strip()[:500])
                elif b.get("type") == "thinking":
                    n = len(b.get("thinking") or "")
                    self.write(f"  (thinking{f', {n:,} chars' if n else ''})")
        elif et == "user":
            content = ev.get("message", {}).get("content", [])
            for b in content if isinstance(content, list) else []:
                if b.get("type") != "tool_result":
                    continue
                name, started = self.tools.pop(b.get("tool_use_id", ""), ("tool", now))
                dt = now - started
                self.tool_time += dt
                c = b.get("content")
                parts = c if isinstance(c, list) else [{"type": "text", "text": str(c or "")}]
                chars = sum(len(x.get("text") or "") for x in parts if x.get("type") == "text")
                images = sum(1 for x in parts if x.get("type") == "image")
                what = ", ".join(x for x in (f"{chars:,} chars" if chars else "", f"{images} image(s)" if images else "") if x) or "empty"
                err = (" · ERROR: " + re.sub(r"\s+", " ", str(c))[:300]) if b.get("is_error") else ""
                self.write(f"  ← {name} result in {dt:.1f}s · {what}{err}")
        elif et == "result":
            u = ev.get("usage") or {}
            self.write("─" * 60)
            self.write(f"Finished: {ev.get('subtype', '')}{' (error)' if ev.get('is_error') else ''} · "
                       f"{(ev.get('duration_ms') or 0) / 1000:.0f}s total, {(ev.get('duration_api_ms') or 0) / 1000:.0f}s in the API · "
                       f"{ev.get('num_turns', 0)} turns · cost ${ev.get('total_cost_usd') or 0:.2f}")
            self.write(f"Tokens: {u.get('input_tokens', 0):,} uncached in, {u.get('cache_read_input_tokens', 0):,} cached in, "
                       f"{u.get('cache_creation_input_tokens', 0):,} newly cached, {u.get('output_tokens', 0):,} out")
            self.summary()

    def summary(self):
        total = time.time() - self.t0
        self.write(f"Where the time went: model calls {self.model_time:.0f}s ({self.calls} calls, {self.out_tokens:,} tokens "
                   f"written) · tools {self.tool_time:.0f}s · other {max(0.0, total - self.model_time - self.tool_time):.0f}s")

    def close(self):
        if not self.f.closed:
            self.f.close()


# ── API engine (no Claude Code): Gemini / OpenAI / DeepSeek / … ─────────────
#
# A long transcript is written part by part: a model works worse on a long input, and its reply
# has an output limit. Each part gets only its slice of the transcript (and of the subtitles /
# external transcripts / screenshots) and writes the body of that part; a last small call writes
# the frame (head with CSS, header, footer), and the parts are joined into it. Any reply that
# was cut off at the output limit is continued from where it stopped.

LOOP_CHECK = 4000         # chars between checks that the model isn't going round in circles
LOOP_TAIL = 400           # a reply whose last this-many chars already came twice before is a loop
class Stuck(RuntimeError):
    """The model keeps writing the same text over and over."""


CHUNK_CHARS = 25000      # transcript chars per part (~25 min of speech); up to 1.3× that is one part
MAX_CONTINUE = 4         # continuations of a reply that was cut off
BODY_MARK = "<!--BODY-->"
CONTINUE_MSG = ("Your reply was cut off at the output limit. Continue EXACTLY where it stopped, mid-word if "
                "needed: don't repeat anything, don't start over, no comments, no markdown fences.")

API_CHUNK = """## This call: part {part} of {parts}
The body is written part by part and the parts are joined afterwards. This part covers {t_from}–{t_to} (the RAW TRANSCRIPT below is only this part).
- Write ONLY this part's body: numbered section dividers, speaker turns, callouts and screenshot figures, as HTML with the classes of the style reference. No <!DOCTYPE>, <html>, <head>, <style>, header or footer: those are made separately (requirement 6 doesn't apply to this call).
- Number the sections from {next_section}.{prev_note}
- Speakers identified so far (keep exactly these names; add new people as you identify them):
{speakers}
Reply with the HTML fragment only, no markdown fences."""

API_PREV = """
- The previous part ended like this (don't repeat it; if that topic continues, carry on without a new section divider - then number your first new section from {next_section}):
{tail}"""

API_SHELL = """Make the frame of an annotated transcript document whose body is already written: one HTML document with
- <!DOCTYPE html>, <head> with a <title> and ALL the CSS of the style reference below (light and dark mode, every class it defines: the body uses them),
- the header as in the style reference: title, subtitle, date and time range, "Who's speaking" chips, callout legend with only these callout types: {callout_types}, hint,
- then a line with exactly {body_mark} where the body goes,
- the footer: provenance (source transcript {source}, {n_lines} lines, {t_first}–{t_last}, version {v}, generated {now}) and notes on speaker attribution and corrected terms.
Write the title and subtitle in the language of the speech.
{date}
Speakers (name · role); write each name in the chips exactly as given, it is how the body names them:
{speakers}
Sections:
{sections}

Reply with the HTML only, no markdown fences."""

# The "app builds the frame" layout (setting api_layout = app): the model gets this instead of the
# style reference, so there is no placeholder header to fill in, and these placeholders are easy to
# spot (TEMPLATE_LEFTOVERS) when a model copies them.
FIGURE_EXAMPLE = """<figure class="shot"><img src="[SRC]" alt="[WHAT IS SHOWN]" loading="lazy"><figcaption><span class="time">[HH:MM:SS]</span>[WHAT IS SHOWN AND WHY IT MATTERS]</figcaption></figure>

"""
BODY_MARKUP = """Replace every [BRACKETED] part with content from the RAW TRANSCRIPT, in the language of the speech. Speaker classes: us = the person recording ([Me]) and their team, them = the other party, other = an unidentified voice.
Speaker names and roles are plain words, never the transcript's tags like [Me] or [Speaker 1]: the real name when it is said, otherwise a descriptive label.

<div class="section">1 · [SECTION TITLE]</div>

<div class="turn"><span class="who us">[SPEAKER NAME]</span><span class="org">[ROLE]</span><span class="time">[HH:MM:SS OF ITS FIRST LINE]</span>
<p>[WHAT WAS SAID]</p></div>

""" + FIGURE_EXAMPLE + """<aside class="callout summary"><div class="top"><span class="badge summary">📝 [THE WORD SUMMARY]</span><span class="lead">[ONE-SENTENCE TAKEAWAY]</span></div>
<p>[KEY POINTS, DECISIONS, NUMBERS AND ACTION ITEMS, WITH <b>KEY TERMS</b> IN BOLD]</p></aside>

<aside class="callout new"><div class="top"><span class="badge new">🆕 [THE WORD NEW]</span><span class="lead">[ONE-SENTENCE TAKEAWAY]</span></div>
<p>[DETAIL]</p><p class="prior"><b>[THE WORDS PRIOR VIEW]:</b> [WHAT WE KNEW BEFORE, AND WHERE FROM]</p></aside>
(✅ confirm and 🔄 update callouts: the same, with class "confirm" / "update".)"""

API_TITLE = """Below are the section titles and takeaways of a transcript document. Write the document's title (at most 8 words, naming what the session was about) and a one-line subtitle, both in {language}.
Reply with exactly two lines, the title and then the subtitle: no quotes, no labels, no markdown.

{sections}"""

TEMPLATES.update({
    "api_chunk": {
        "title": "API: one part", "default": API_CHUNK,
        "help": "API engine: added to each part's request for a long transcript, and to every request when the app builds the frame (the requirements of the main prompt come first).",
        "placeholders": {"part": "part number", "parts": "number of parts", "t_from": "time the part starts",
                         "t_to": "time the part ends", "next_section": "number of the part's first section",
                         "prev_note": "how the previous part ended (empty for part 1)",
                         "speakers": "speakers found in the earlier parts"},
    },
    "api_shell": {
        "title": "API: header & footer", "default": API_SHELL,
        "help": "API engine, long transcripts: the last call, which writes the head, header and footer around the parts.",
        "placeholders": {"body_mark": "the marker where the body goes (must stay in the reply)",
                         "callout_types": "callout types the body uses", "source": "transcript file name",
                         "n_lines": "number of lines", "t_first": "first time", "t_last": "last time",
                         "v": "version", "now": "date and time", "date": "recording date line",
                         "speakers": "speakers with roles", "sections": "section titles"},
    },
    "api_title": {
        "title": "API: title (app builds the frame)", "default": API_TITLE,
        "help": "API engine, when the app builds the frame: the last, short call that names the document.",
        "placeholders": {"sections": "section titles with their one-line takeaways",
                         "language": "the language of the speech (\"Russian\", or \"the same language as the sections\")"},
    },
})

LINE_TIME_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]")


def _blocks(text: str) -> list[tuple[str, str]]:
    """(time, text) per transcript line; continuation lines stay with their line."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines(keepends=True):
        m = LINE_TIME_RE.match(line)
        if m or not out:
            out.append((m.group(1) if m else "00:00:00", line))
        else:
            out[-1] = (out[-1][0], out[-1][1] + line)
    return out


def split_transcript(text: str, budget: int = CHUNK_CHARS) -> list[str]:
    """Parts of about `budget` chars, cut at the longest pause near each boundary."""
    blocks = _blocks(text)
    if len(text) <= budget * 1.3:
        return [text]
    parts, cur, size = [], [], 0
    i = 0
    while i < len(blocks):
        cur.append(blocks[i])
        size += len(blocks[i][1])
        rest = sum(len(b[1]) for b in blocks[i + 1:])
        if size >= budget * 0.8 and rest > budget * 0.3:
            # look ahead up to 1.2× budget for the longest pause, and cut there
            best, best_gap, s = i, -1, size
            for k in range(i, len(blocks) - 1):
                if k > i:
                    s += len(blocks[k][1])
                if s > budget * 1.2 and k > i:
                    break
                gap = _secs(blocks[k + 1][0]) - _secs(blocks[k][0])
                if gap > best_gap:
                    best, best_gap = k, gap
            cur += blocks[i + 1:best + 1]
            parts.append("".join(b[1] for b in cur))
            cur, size, i = [], 0, best + 1
            continue
        i += 1
    if cur:
        parts.append("".join(b[1] for b in cur))
    return parts


def _slice_ext(text: str, t_from: str, t_to: str, same_timeline: bool, frac: tuple[float, float]) -> str:
    """The piece of an external transcript that matches a part: by time when it shares our
    timeline (YouTube), else by position with a margin (a meeting tool's own clock)."""
    blocks = _blocks(text)
    if not blocks:
        return ""
    if same_timeline:
        lo, hi = max(0, _secs(t_from) - 60), _secs(t_to) + 60
        return "".join(b for t, b in blocks if lo <= _secs(t) <= hi)
    n = len(blocks)
    a, b = max(0, int((frac[0] - 0.05) * n)), min(n, int((frac[1] + 0.05) * n) + 1)
    return "".join(x for _, x in blocks[a:b])


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0].rstrip()
    return text


def _join(acc: str, more: str) -> str:
    """Append a continuation, dropping what it repeated from the end of the text so far."""
    more = _strip_fences(more) if not acc else more.lstrip("`").removeprefix("html\n")
    for n in range(min(400, len(acc), len(more)), 15, -1):
        if acc.endswith(more[:n]):
            return acc + more[n:]
    return acc + more


def _body_of(fragment: str) -> str:
    """A part's body, in case the model wrapped it in a document anyway."""
    m = re.search(r"<body[^>]*>(.*?)(?:</body>|$)", fragment, re.S | re.I)
    if m:
        fragment = m.group(1)
    fragment = re.sub(r"<footer\b.*?</footer>", "", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<header\b.*?</header>", "", fragment, flags=re.S | re.I).strip()
    m = re.fullmatch(r'<div class="wrap">(.*)</div>', fragment, re.S)
    return m.group(1).strip() if m else fragment


def _speakers(html: str) -> list[str]:
    seen = {}
    for m in re.finditer(r'<span class="who[^"]*">([^<]+)</span>(?:\s*<span class="org">([^<]*)</span>)?', html):
        name = m.group(1).strip()
        if name and name not in seen:
            seen[name] = (m.group(2) or "").strip()
    return [f"{n} · {r}" if r else n for n, r in seen.items()]


# The frame's own words, by the language of the speech (others get English).
FRAME_WORDS = {
    "en": {"who": "Who's speaking", "legend": "How to read the callouts",
           "summary": "The gist of the topic above: key points, decisions, numbers, action items.",
           "new": "Something we did not have in the source material before this session.",
           "confirm": "Confirms / adds colour to something already documented.",
           "update": "Changes, narrows, or contradicts what prior sources said - worth reconciling.",
           "badge": {"summary": "📝 SUMMARY", "new": "🆕 NEW", "confirm": "✅ CONFIRMS", "update": "🔄 UPDATES"},
           "footer": "Built from the transcript {source} ({n} lines, {t1}–{t2}) · version {v} · {now}",
           "unknown": "Speaker",
           "months": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                      "October", "November", "December"], "date": "{month} {d}, {y}"},
    "ru": {"who": "Кто говорит", "legend": "Как читать выноски",
           "summary": "Суть темы выше: главное, решения, цифры, задачи.",
           "new": "То, чего не было в прежних материалах.",
           "confirm": "Подтверждает или дополняет уже известное.",
           "update": "Меняет, уточняет или опровергает прежние источники - стоит свериться.",
           "badge": {"summary": "📝 ИТОГ", "new": "🆕 НОВОЕ", "confirm": "✅ ПОДТВЕРЖДАЕТ", "update": "🔄 МЕНЯЕТ"},
           "footer": "Собрано из транскрипта {source} ({n} строк, {t1}–{t2}) · версия {v} · {now}",
           "unknown": "Собеседник",
           "months": ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
                      "октября", "ноября", "декабря"], "date": "{d} {month} {y}"},
}


FIGURE_RE = re.compile(r'<figure class="shot"><img src="([^"]*)"[^>]*>\s*<figcaption>(.*?)(?:</figcaption>\s*</figure>|(?=\n\s*\n)|$)', re.S)


def tidy_body(body: str, lang: str, srcs: set[str]) -> str:
    """What a small model leaves in the body: tags as speaker names, example text, English badges,
    speech put into a screenshot figure that doesn't exist."""
    def name(m):
        text = re.sub(r"\[([^\]<]*)\]", r"\1", m.group(2))
        text = re.sub(r"\s*\((?:Me|Them|Other|Speaker \d+)\)\s*$", "", text).strip()
        return m.group(1) + text + m.group(3)
    body = re.sub(r'(<span class="(?:who\b[^"]*|org)">)([^<]*)(</span>)', name, body)

    def figure(m):
        if m.group(1) in srcs:
            return m.group(0)
        t = re.search(r'<span class="time">(\d{2}:\d{2}:\d{2})</span>', m.group(2))
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        text = text.removeprefix(t.group(1)).strip() if t else text
        text = re.sub(r'"?\s*what is shown and why it matters\s*$', "", text, flags=re.I)   # requirement 5's example
        return (f'<div class="turn"><span class="who other">{FRAME_WORDS[lang]["unknown"]}</span><span class="org"></span>'
                f'<span class="time">{t.group(1) if t else ""}</span>\n<p>{text}</p></div>')
    body = FIGURE_RE.sub(figure, body)

    seen = set()
    def once(m):      # the same turn again, e.g. a later section that starts over
        key = (re.findall(r'<span class="time">([^<]*)</span>', m.group(0))[:1],
               re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(0).split("</span>")[-1])).strip())
        key = (tuple(key[0]), key[1])
        if key in seen:
            return ""
        seen.add(key)
        return m.group(0)
    body = re.sub(r'<div class="turn">.*?</p>\s*</div>\s*', once, body, flags=re.S)
    body = re.sub(r"<p>\s*Key points, decisions[^<]*(?:<b>key terms</b>[^<]*)?</p>\s*", "", body)
    for k, text in FRAME_WORDS[lang]["badge"].items():
        body = re.sub(rf'(<span class="badge {k}\b[^"]*">)\s*{re.escape(FRAME_WORDS["en"]["badge"][k])}\s*(</span>)',
                      lambda m: m.group(1) + text + m.group(2), body)
    return body


def speech_lang(text: str) -> str:
    letters = re.findall(r"[^\W\d_]", text)
    cyr = sum("Ѐ" <= c <= "ӿ" for c in letters)
    return "ru" if letters and cyr / len(letters) > 0.3 else "en"


def app_frame(body: str, style_html: str, lang: str, title: str, sub: str, dt: datetime | None,
              t_first: str, t_last: str, source: str, n_lines: int, v: int) -> str:
    """The whole document around a body the model wrote: CSS of the style reference, header, footer."""
    w = FRAME_WORDS[lang]
    css = "\n".join(re.findall(r"<style\b[^>]*>.*?</style>", style_html, re.S | re.I))
    when = " · ".join(x for x in (
        fill(w["date"], d=dt.day, month=w["months"][dt.month - 1], y=dt.year) if dt else "",
        f"{t_first}–{t_last}" if t_first and t_first != "-" else "") if x)
    chips, seen = [], set()
    for m in re.finditer(r'<span class="who (us|them|other)\b[^"]*">([^<]+)</span>(?:\s*<span class="org">([^<]*)</span>)?', body):
        side, name, role = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
        if name not in seen:
            seen.add(name)
            chips.append(f'<span class="chip {side}">{name}{f" - {role}" if role and role != name else ""}</span>')
    kinds = [k for k in ("summary", "new", "confirm", "update") if re.search(rf'class="callout {k}\b', body)]
    legend = ""
    if [k for k in kinds if k != "summary"]:     # a SUMMARY alone needs no explaining
        def badge(k):
            m = re.search(rf'<span class="badge {k}\b[^"]*">([^<]+)</span>', body)
            return m.group(1).strip() if m else w["badge"][k]
        legend = (f'  <div class="legend">\n    <h3>{w["legend"]}</h3>\n'
                  + "".join(f'    <div class="item"><span class="badge {k}">{badge(k)}</span><span>{w[k]}</span></div>\n'
                            for k in kinds) + "  </div>\n")
    who = (f'  <div class="attendees">\n    <h3>{w["who"]}</h3>\n    <div class="att-row">\n'
           + "".join(f"      {c}\n" for c in chips) + "    </div>\n  </div>\n") if chips else ""
    footer = fill(w["footer"], source=f"<code>{_esc(source)}</code>", n=n_lines, t1=t_first, t2=t_last, v=v,
                  now=datetime.now().strftime("%Y-%m-%d %H:%M"))
    return (f'<!doctype html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{_esc(title)}</title>\n{css}\n</head>\n<body>\n<div class=\"wrap\">\n\n"
            f'<header class="doc">\n  <h1>{_esc(title)}</h1>\n'
            + (f'  <p class="sub">{_esc(sub)}</p>\n' if sub else "")
            + (f'  <p class="meta">{when}</p>\n' if when else "")
            + who + legend + "</header>\n\n" + body + f"\n\n<footer>\n  {footer}\n</footer>\n\n</div>\n</body>\n</html>\n")


def run_api(spec: dict, on_progress=None, log: RunLog | None = None) -> dict:
    """Beautify with an API model (llm_api), part by part for a long transcript.
    on_progress(chars, note) while it writes. Returns {"warning": …} when something was cut off."""
    settings = spec["settings"]
    cfg = llm_api.config(settings)
    text = spec["input"].read_text(encoding="utf-8", errors="replace")
    yt = spec.get("yt_sources", {})
    exts = [(p, p.read_text(encoding="utf-8", errors="replace")) for p in spec.get("ext_files", [])]
    style = spec["style"].read_text(encoding="utf-8")
    P = load_prompts(spec["project"])
    # The requirements part of the (maybe customised) prompt, without the Claude Code file handling
    req = spec["prompt"]
    req = (req.split("## Requirements", 1)[1] if "## Requirements" in req else "\n" + req)
    req = (req.replace("Use the exact relative src listed for each screenshot above.",
                       "Use the exact src listed for each screenshot in SCREENSHOTS below.")
           .split("8. Output file:")[0])
    dt = sid_datetime(spec["sid"])
    date_line = f"Recording date: {dt:%Y-%m-%d} (times in the transcript are local clock times).\n" if dt else ""
    notes = load_notes(spec["project"]).strip()

    total = [0]
    note = [""]
    thought = [0, 0]
    warnings: list[str] = []
    reply: list[str] = []       # the current reply, for the loop check
    cancel = spec.get("cancel")  # threading.Event from the server's Cancel

    def delta(d):
        total[0] += len(d)
        reply.append(d)
        n = sum(map(len, reply))
        if n // LOOP_CHECK != (n - len(d)) // LOOP_CHECK:
            so_far = "".join(reply)
            if so_far.count(so_far[-LOOP_TAIL:]) >= 3:
                raise Stuck(f"The model got stuck repeating itself ({n:,} characters into the reply); "
                            "try again, or another model")
        if on_progress:
            on_progress(total[0], note[0])

    def reasoning(r):
        if log and not thought[0]:
            log.write("  the model is thinking (no text yet)")
        thought[0] += len(r)
        if log and thought[0] - thought[1] >= 20000:
            thought[1] = thought[0]
            log.write(f"  {thought[0]:,} chars of thinking")

    def call(prompt: str, label: str, must_end: str | None = None) -> str:
        """One reply; continued while it is cut off at the output limit."""
        note[0] = label
        messages = [{"role": "user", "content": prompt}]
        acc = ""
        if log:
            log.write(f"{label}: request {len(prompt):,} chars (~{len(prompt) // 4:,} tokens)")
        for attempt in range(MAX_CONTINUE + 1):
            info: dict = {}
            thought[0] = thought[1] = 0
            started, before = time.time(), total[0]
            reply.clear()
            try:
                part = llm_api.stream(cfg, messages, delta, reasoning, info=info,
                                      should_stop=cancel.is_set if cancel else None)
            except llm_api.Cancelled:
                if log:
                    log.write(f"{label}: cancelled after {total[0] - before:,} chars")
                raise
            except Stuck as e:
                if log:
                    log.write(f"{label}: {e}")
                raise
            except Exception as e:
                if log:
                    log.write(f"{label}: failed: {llm_api.explain(e)}")
                raise RuntimeError(llm_api.explain(e)) from e
            acc = _join(acc, part)
            cut = info.get("finish_reason") == "length" or (must_end and must_end not in acc[-3000:].lower())
            if log:
                log.write(f"{label}: {total[0] - before:,} chars in {time.time() - started:.0f}s"
                          + (f" after {thought[0]:,} chars of thinking" if thought[0] else "")
                          + f" · finish: {info.get('finish_reason') or '?'}" + (" · CUT OFF" if cut else ""))
            if not cut:
                return acc
            if attempt == MAX_CONTINUE:
                break
            note[0] = f"{label} (continuing)"
            messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": acc},
                        {"role": "user", "content": CONTINUE_MSG}]
        warnings.append(f"{label} was still cut off after {MAX_CONTINUE} continuations")
        if log:
            log.write(f"{label}: still cut off after {MAX_CONTINUE} continuations, kept as is")
        return acc

    def checked(prompt: str, label: str, must_end: str | None = None, t_from: str = "", t_to: str = "") -> str:
        """call(), asked once more when the model filled in the style reference instead of the transcript."""
        out = call(prompt, label, must_end)
        why = copied_template(out, t_from, t_to)
        if not why:
            return out
        if log:
            log.write(f"{label}: {NOT_FROM_TRANSCRIPT} ({why}), asking again")
        out = call(prompt + f"\n\n## YOUR PREVIOUS ATTEMPT WAS WRONG\nIn it {why}: it filled in the style "
                   "reference instead of writing up the RAW TRANSCRIPT. The style reference's text is placeholder "
                   "only. Every speaker, time and sentence must come from the RAW TRANSCRIPT, in its language.",
                   f"{label} (again)", must_end)
        why = copied_template(out, t_from, t_to)
        if why:
            warnings.append(f"{NOT_FROM_TRANSCRIPT}: {why}; try a stronger model")
        return out

    def inputs(part_text: str, t_from: str, t_to: str, frac: tuple[float, float], whole: bool) -> str:
        shots = [x for x in spec["shots"] if whole or t_from <= x["time"] <= t_to]
        srcs = "".join(f"  {x['time']}  {Path(x['path']).name}  → src=\"{x['src']}\"\n" for x in shots)
        ocr = []
        notes = spec.get("screen_notes", "")
        if notes:
            notes = notes if whole else image_notes.notes_between(notes, t_from, t_to)
        else:     # no condensed notes: the screenshots' own OCR text
            for x in shots:
                txt = Path(x["path"]).with_suffix(".txt")
                if txt.exists():
                    ocr.append(f"--- {Path(x['path']).name} ---\n{txt.read_text(encoding='utf-8', errors='replace')}")
        ext = ""
        for p, body in exts:
            src = yt.get(str(p))
            piece = body if whole else _slice_ext(body, t_from, t_to, bool(src), frac)
            if piece.strip():
                ext += (f"--- {p.name} ({EXT_KIND.get(src, EXT_KIND[None])})"
                        + ("" if whole else ", only the piece around this part") + f" ---\n{piece}\n")
        return (f"## SCREENSHOTS (time, file, src)\n{srcs or '(none)'}\n"
                + (f"## WHAT WAS ON SCREEN (notes from the screenshots and auto frames, by time; not speech)\n"
                   f"{notes or '(nothing in this part)'}\n\n"
                   if spec.get("screen_notes") else "## SCREENSHOT OCR\n" + ("\n\n".join(ocr) or "(none)") + "\n\n")
                + f"## RAW TRANSCRIPT\n{part_text}\n"
                + (f"\n## EXTERNAL TRANSCRIPTS (how to use each is in its heading; keep OUR times)\n{ext}" if ext else ""))

    def title_of(body: str) -> tuple[str, str]:
        """(title, subtitle) from a short call; the first section's title if that fails."""
        plain = lambda x: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x)).strip()
        sections = [plain(x) for x in re.findall(r'<div class="section[^"]*">(.*?)</div>', body, re.S)]
        leads = [plain(x) for x in re.findall(r'<span class="lead">(.*?)</span>', body, re.S)]
        fallback = (re.sub(r"^\d+\s*·\s*", "", sections[0]) if sections else spec["input"].stem, "")
        lines = "\n".join(f"  {x}" for x in sections[:40]) + "\n" + "\n".join(f"  - {x}" for x in leads[:40])
        try:
            reply = call(fill(P["api_title"], sections=lines.strip("\n") or "(none)",
                              language="Russian" if lang == "ru" else "the same language as the sections"), "Title")
        except llm_api.Cancelled:
            raise
        except Exception:
            return fallback      # call() has logged why
        got = [x.strip().strip('"«»*#').strip() for x in _strip_fences(reply).splitlines() if x.strip()]
        if not got or len(got[0]) > 150 or "<" in got[0]:
            if log:
                log.write("Title: no usable title in the reply, the first section's is used")
            return fallback
        return got[0], (got[1] if len(got) > 1 and len(got[1]) <= 300 and "<" not in got[1] else "")

    parts = split_transcript(text)
    by_app = settings.get("api_layout") == "app"     # the model writes only the body
    timed = re.findall(r"(?m)^\[(\d{2}:\d{2}:\d{2})\]", text)
    lang = speech_lang(text)
    head = "Create a FULL annotated transcript as one self-contained HTML document.\n" + date_line
    if lang == "ru":    # small models write the titles and callouts in English otherwise
        head += ("The speech is in Russian: write ALL the document's text in Russian (section titles, roles, "
                 "callouts), not in English.\n")
    if len(parts) == 1 and not by_app:
        if log:
            log.write(f"Transcript {len(text):,} chars: one request")
        prompt = (head + "\n## Requirements" + req + "\nReply with the HTML document only, with no markdown fences.\n\n"
                  f"## STYLE REFERENCE (structure/CSS only)\n{style}\n\n" + inputs(text, "", "", (0, 1), True))
        html = checked(prompt, "Document", "</html>", *((timed[0], timed[-1]) if timed else ()))
    else:
        if log:
            log.write(f"Transcript {len(text):,} chars: "
                      + (f"{len(parts)} parts of ~{CHUNK_CHARS:,} chars" if len(parts) > 1 else "one request")
                      + (" · the model writes the conversation, the app builds the frame" if by_app else ""))
        markup = (f"## STYLE REFERENCE (the body's markup)\n{BODY_MARKUP}" if by_app
                  else f"## STYLE REFERENCE (structure, classes and markup only)\n{style}")
        if by_app and not spec["shots"]:
            markup = (markup.replace(FIGURE_EXAMPLE, "")
                      + "\n\nThere are no screenshots in this session: write no <figure> at all, every line is a turn.")
        bodies: list[str] = []
        offset = 0
        for k, part_text in enumerate(parts, 1):
            times = re.findall(r"(?m)^\[(\d{2}:\d{2}:\d{2})\]", part_text)
            t_from, t_to = (times[0], times[-1]) if times else ("", "")
            frac = (offset / len(text), (offset + len(part_text)) / len(text))
            offset += len(part_text)
            done = "\n".join(bodies)
            speakers = _speakers(done)
            next_section = len(re.findall(r'class="section\b', done)) + 1
            prev = fill(API_PREV, next_section=next_section, tail=done[-2500:]) if bodies else ""
            task = fill(P["api_chunk"], part=k, parts=len(parts), t_from=t_from, t_to=t_to,
                        next_section=next_section, prev_note=prev,
                        speakers="\n".join(f"  {x}" for x in speakers) or "  (none yet)")
            prompt = (head + "\n## Requirements" + req + "\n" + task + "\n\n" + markup + "\n\n"
                      + inputs(part_text, t_from, t_to, frac, len(parts) == 1))
            label = f"Part {k}/{len(parts)} ({t_from}–{t_to})" if len(parts) > 1 else "Conversation"
            bodies.append(_body_of(_strip_fences(checked(prompt, label, t_from=t_from, t_to=t_to))))
        body = "\n".join(bodies)
    if by_app:
        body = tidy_body(body, lang, {x["src"] for x in spec["shots"]})
        html = app_frame(body, style, lang, *title_of(body), dt, timed[0] if timed else "",
                         timed[-1] if timed else "", spec["input"].name, len(timed), spec["v"])
    elif len(parts) > 1:
        callouts = sorted(set(re.findall(r'class="callout (\w+)', body)))
        sections = re.findall(r'<div class="section[^"]*">(.*?)</div>', body, re.S)
        shell_prompt = fill(
            P["api_shell"], body_mark=BODY_MARK, callout_types=", ".join(callouts) or "none",
            source=spec["input"].name, n_lines=len(timed), t_first=timed[0] if timed else "",
            t_last=timed[-1] if timed else "", v=spec["v"], now=datetime.now().strftime("%Y-%m-%d %H:%M"),
            date=date_line.strip(), speakers="\n".join(f"  {x}" for x in _speakers(body)) or "  (unknown)",
            sections="\n".join(f"  {re.sub(r'<[^>]+>', '', x).strip()}" for x in sections) or "  (none)")
        if notes:
            shell_prompt += f"\n\n## THE USER'S NOTES (names and terms)\n{notes}"
        shell_prompt += f"\n\n## STYLE REFERENCE\n{style}"
        shell = _strip_fences(checked(shell_prompt, "Header & footer", "</html>"))
        if BODY_MARK in shell:
            html = shell.replace(BODY_MARK, "\n" + body + "\n", 1)
        else:
            if log:
                log.write(f"Header & footer: no {BODY_MARK} marker, body put before the footer")
            at = next((i for i in (shell.lower().rfind("<footer"), shell.lower().rfind("</body>")) if i >= 0), len(shell))
            html = shell[:at] + "\n" + body + "\n" + shell[at:]
    html = _strip_fences(html)
    if "</html>" not in html[-3000:].lower():
        warnings.append("the document has no end (</html>)")
    # Not fix_unrendered_templates(): it turns "${...}" in the text into running script, and
    # the text comes from the transcript. Documents are static HTML (/files serves no script).
    spec["out"].write_text(html, encoding="utf-8")
    if log:
        log.write(f"Document: {len(html):,} chars" + (f" · WARNING: {'; '.join(warnings)}" if warnings else ""))
    return {"warning": "; ".join(warnings)} if warnings else {}


# ── Console entry point ──────────────────────────────────────────────────────

def main():
    import native_helpers
    native_helpers.utf8_stdio()    # Windows pipes: cp1251 can't print → or Cyrillic
    ap = argparse.ArgumentParser(description="Beautify a transcript into a full annotated HTML document")
    ap.add_argument("project")
    ap.add_argument("sid", help="Session id, e.g. 20260928_110051 (or 'latest')")
    ap.add_argument("--mode", choices=["update", "full", "final"], default="full")
    ap.add_argument("--model", default=None, help="Claude model alias (default from settings: opus)")
    ap.add_argument("--layout", choices=["model", "app"], default=None,
                    help="API engine: who builds the frame (default from settings)")
    ap.add_argument("--embed", type=int, metavar="N",
                    help="Don't beautify: save vN.html as vN-emb.html with the images embedded, to share as one file")
    args = ap.parse_args()

    sid = args.sid
    if sid == "latest":
        from sessions import list_sessions
        sid = list_sessions(args.project)[0]["sid"]
    if args.embed is not None:
        out = embed_version(args.project, sid, args.embed)
        print(f"{out}  ({out.stat().st_size / 1e6:.1f} MB)")
        return
    spec = prepare(args.project, sid, args.mode)
    if args.model:
        spec["settings"]["beautify_model"] = args.model
    if args.layout:
        spec["settings"]["api_layout"] = args.layout
    print(f"Beautify v{spec['v']} ({spec['mode']}) → {spec['out']}")
    started = time.time()

    log = RunLog(spec["log"])
    print(f"Log: {spec['log']}")
    screen_notes(spec, log)
    if spec["settings"]["beautify_engine"] == "api":
        log.header(spec, "api", llm_api.config(spec["settings"])["model"])
        try:
            extra = run_api(spec, log=log)
            rec = finish(args.project, sid, spec["v"], "done", duration=round(time.time() - started), **extra)
        except Exception as e:
            rec = finish(args.project, sid, spec["v"], "failed", error=str(e))
    else:
        cmd = claude_command(spec)
        log.header(spec, "claude", spec["settings"]["beautify_model"], cmd)
        proc = subprocess.Popen(cmd, cwd=spec["cwd"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        proc.stdin.write(spec["prompt"])
        proc.stdin.close()
        result = {}
        for line in proc.stdout:
            ev = claude_cli.parse_line(line)
            if not ev:
                continue
            log.event(ev)
            for name, inp in claude_cli.tool_uses(ev):
                print(f"  [{time.time() - started:6.0f}s] {claude_cli.describe_tool(name, inp, spec['labels'])}")
            if ev.get("type") == "result":
                result = ev
        proc.wait()
        if not result:
            log.summary()
        ok = proc.returncode == 0 and not result.get("is_error")
        rec = finish(args.project, sid, spec["v"], "done" if ok else "failed",
                     duration=round(time.time() - started), summary=(result.get("result") or "")[:500],
                     cost=result.get("total_cost_usd"))
    if rec.get("frames_added"):
        log.write(f"Inserted {rec['frames_added']} auto frames into the document")
    log.write(f"Status: {rec['status']} {rec.get('error', '')}")
    log.close()
    print(f"Status: {rec['status']}  {rec.get('summary', rec.get('error', ''))}")
    print(f"Output: {spec['out']}")
    if rec.get("docs_copy"):
        print(f"Docs copy: {rec['docs_copy']}")
    sys.exit(0 if rec["status"] == "done" else 1)


if __name__ == "__main__":
    main()
