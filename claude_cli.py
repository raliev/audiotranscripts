"""Thin helpers around the Claude Code CLI in headless mode (`claude -p`).

Running through Claude Code (rather than the raw API) gives the model the same
tools as an interactive session: reading screenshots, writing files, and any
MCP servers configured for the working directory (e.g. claude-context).
"""

import json
import os
import shutil
from pathlib import Path


def claude_binary() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for p in ("~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
              "~/.claude/local/claude"):
        p = os.path.expanduser(p)
        if os.access(p, os.X_OK):
            return p
    return None


def build_command(model: str, allowed_tools: list[str], add_dirs: list[str | Path] = (),
                  resume: str | None = None, append_system: str | None = None,
                  partial: bool = True, accept_edits: bool = False) -> list[str]:
    exe = claude_binary()
    if not exe:
        raise RuntimeError("Claude Code CLI ('claude') not found in PATH")
    cmd = [exe, "-p", "--output-format", "stream-json", "--verbose"]
    if partial:
        cmd.append("--include-partial-messages")
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
        # Only what's allowed should be visible, so the model doesn't waste turns on denied tools.
        cmd += ["--disallowedTools", "Bash,WebFetch,WebSearch,NotebookEdit"]
    if accept_edits:
        cmd += ["--permission-mode", "acceptEdits"]
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    if resume:
        cmd += ["--resume", resume]
    if append_system:
        cmd += ["--append-system-prompt", append_system]
    return cmd


def parse_line(line: str | bytes) -> dict | None:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def text_delta(ev: dict) -> str | None:
    """Streaming text chunk from a stream_event, if any."""
    if ev.get("type") != "stream_event":
        return None
    inner = ev.get("event", {})
    if inner.get("type") == "content_block_delta":
        d = inner.get("delta", {})
        if d.get("type") == "text_delta":
            return d.get("text", "")
    return None


def tool_uses(ev: dict) -> list[tuple[str, dict]]:
    """(name, input) for each tool call in a complete assistant message."""
    if ev.get("type") != "assistant":
        return []
    out = []
    for block in ev.get("message", {}).get("content", []) or []:
        if block.get("type") == "tool_use":
            out.append((block.get("name", ""), block.get("input", {}) or {}))
    return out


def generic_label(name: str) -> str:
    """Placeholder label shown as soon as a tool call starts streaming."""
    if name == "Read":
        return "Reading…"
    if name == "Write":
        return "Writing document…"
    if name in ("Edit", "MultiEdit"):
        return "Editing document…"
    if name.startswith("mcp__claude-context__"):
        return "Searching project knowledge…"
    return describe_tool(name, {})


def describe_tool(name: str, inp: dict, labels: dict[str, str] | None = None) -> str:
    """Human-readable progress line for a tool call. *labels* maps file paths
    (or basenames) to friendly names, e.g. the transcript snapshot."""
    labels = labels or {}

    def label_for(path: str) -> str | None:
        return labels.get(path) or labels.get(Path(path).name)

    if name == "Read":
        path = inp.get("file_path", "")
        if label_for(path):
            return label_for(path)
        if path.lower().endswith((".png", ".jpg", ".jpeg")):
            return f"Looking at {Path(path).name}"
        return f"Reading {Path(path).name}"
    if name == "Write":
        size = len(inp.get("content", "") or "")
        return f"Writing document ({size / 1024:.0f} KB)"
    if name in ("Edit", "MultiEdit"):
        size = len(inp.get("new_string", "") or "")
        return f"Editing document (+{size / 1024:.1f} KB)" if size > 2048 else "Editing document"
    if name in ("Grep", "Glob"):
        return f"Searching files: {inp.get('pattern', '')}"[:120]
    if name.startswith("mcp__claude-context__"):
        q = inp.get("query") or inp.get("path") or ""
        action = name.split("__")[-1].replace("_", " ")
        return f"Project knowledge ({action}): “{q}”"[:160] if q else f"Project knowledge: {action}"
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"Using {parts[1]} · {parts[-1]}"
    if name == "TodoWrite":
        return "Planning"
    return f"Using {name}"
