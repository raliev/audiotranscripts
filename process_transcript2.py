#!/usr/bin/env python3
"""Process raw transcripts + OCR texts through LLM to produce a clean document."""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECTS_DIR = Path("projects")
INLINE_TS_RE = re.compile(r'^\[(\d{2}:\d{2}:\d{2})\]')

PROVIDER_DEFAULTS = {
    "gemini": "gemini-3.5-flash",
    "openai": "gpt-4.1-mini",
    "openrouter": "google/gemini-2.5-flash",
}

PROMPT = """\
You are an expert technical writer and business analyst. Your task is to transform a RAW speech recognition transcript into a highly polished, clean, and structured meeting protocol (TDD / Architectural Sync).

CRITICAL INSTRUCTIONS:
1. Output ONLY the processed markdown text. No conversational introductions or conclusions.
2. DO NOT output the literal contents of OCR text - use them strictly to correct names, variables, system files, and technical terms.
3. Correct all phonetic mishearings, typos, and broken Russian/English grammar structures.
4. Keep all relevant details. Do not summarize away important technical discussions.
5. Accurately map raw Speaker codes (e.g., Speaker 1, Speaker 11) to actual participant names using the screenshot reference data (match dates, roles, and context).
6. Structure the output into:
   - Metadata Block (Date, project, primary focal points, attendee list mapped from references).
   - High-level Decisions & Key Takeaway Bullet points.
   - Action Items Table (Assignee, Description, Target Date, Status).
   - Cleaned dialogue transcript preserving precise timestamps `[HH:MM:SS]` and speaker mappings.

## RAW TRANSCRIPT TO PROCESS:
{transcript}

## SCREENSHOT OCR REFERENCE MATERIAL (Use for correcting technical names and speaker identities):
{screenshots}
"""

HTML_PROMPT = """\
Convert the provided meeting protocol text into a highly polished, visually stunning, fully responsive, and interactive HTML dashboard.

Requirements:
- Single-File architecture: All CSS/HTML/JS must be self-contained within this one file.
- Clean design system with variable CSS variables, system-neutral fonts (Inter), rounded corners, smooth transitions, and visual hierarchy.
- Responsive Layout: Design fluid widths, container margins, and tap-targets ensuring a high-end desktop and mobile experience.
- The interface must feature three distinct tabs:
  1. "Overview & Decisions" - Presenting metadata cards, key takeaways, participant tags, and a beautifully styled action items table.
  2. "Architecture & Visual Layout" - A visual mock wireframe illustrating the slot configurations (use styled dashed elements and page level slot blocks to match the discussed PLP/CMS systems).
  3. "Transcript" - Containing a live search input box, speaker-specific filtering pill buttons, and the chronological conversation blocks with timestamps and speaker avatars/badges.

Use the style patterns and logic shown in this skeleton:

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Meeting Analytics Dashboard</title>
    <style>
        :root {
            --primary: #4f46e5;
            --primary-hover: #4338ca;
            --primary-light: #e0e7ff;
            --secondary: #0f172a;
            --accent: #f59e0b;
            --bg-main: #f8fafc;
            --bg-card: #ffffff;
            --text-main: #334155;
            --text-dark: #0f172a;
            --text-muted: #64748b;
            --border-color: #e2e8f0;
            --color-default: #475569;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: var(--text-main);
            background-color: var(--bg-main);
            margin: 0;
            padding: 0;
        }
        .container {
            max-width: 1200px;
            margin: 40px auto;
            padding: 0 20px;
        }
        .header-card {
            background: linear-gradient(135deg, var(--secondary) 0%, #1e293b 100%);
            color: #ffffff;
            padding: 40px;
            border-radius: 16px;
            box-shadow: 0 10px 25px rgba(15, 23, 42, 0.15);
            margin-bottom: 30px;
            position: relative;
            overflow: hidden;
        }
        .meta-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-top: 25px;
            border-top: 1px solid rgba(255, 255, 255, 0.1);
            padding-top: 20px;
        }
        .meta-item strong {
            color: #94a3b8;
            display: block;
            font-size: 0.75rem;
            text-transform: uppercase;
            margin-bottom: 4px;
        }
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 25px;
            border-bottom: 2px solid var(--border-color);
            padding-bottom: 2px;
        }
        .tab-btn {
            background: none;
            border: none;
            padding: 12px 24px;
            font-size: 1rem;
            font-weight: 600;
            color: var(--text-muted);
            cursor: pointer;
            transition: all 0.2s ease;
            border-radius: 8px 8px 0 0;
        }
        .tab-btn.active {
            color: var(--primary);
            border-bottom: 3px solid var(--primary);
        }
        .tab-panel {
            display: none;
        }
        .tab-panel.active {
            display: block;
            animation: fadeIn 0.4s ease;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .card {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 30px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            margin-bottom: 30px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }
        th, td {
            padding: 12px 16px;
            border-bottom: 1px solid var(--border-color);
            text-align: left;
        }
        th {
            background-color: var(--bg-main);
            font-weight: 600;
        }
        .badge {
            display: inline-flex;
            align-items: center;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }
        .badge-primary { background-color: var(--primary-light); color: var(--primary); }
        .badge-warning { background-color: #fef3c7; color: #d97706; }
        .badge-danger { background-color: #fee2e2; color: #dc2626; }
        .badge-success { background-color: #d1fae5; color: #059669; }
        
        .wireframe-container {
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background: #f1f5f9;
            padding: 15px;
            margin: 20px 0;
        }
        .wf-slot {
            background-color: #ffffff;
            border: 2px dashed #94a3b8;
            border-radius: 6px;
            padding: 12px;
            text-align: center;
            font-size: 0.85rem;
            margin-bottom: 10px;
        }
        .wf-slot.page-level {
            border-style: solid;
            border-color: #10b981;
            background-color: #ecfdf5;
            color: #065f46;
        }
        
        .dialog-bubble {
            background-color: #f8fafc;
            border-left: 4px solid var(--color-default);
            padding: 16px 20px;
            border-radius: 0 12px 12px 0;
            margin-bottom: 16px;
            transition: all 0.2s ease;
        }
        .dialog-bubble:hover {
            background-color: #f1f5f9;
        }
        .dialog-meta {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 0.85rem;
        }
        .dialog-speaker {
            font-weight: 700;
        }
        .dialog-time {
            font-family: monospace;
            background: #e2e8f0;
            padding: 2px 6px;
            border-radius: 4px;
        }
        .search-box {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .search-input {
            flex-grow: 1;
            padding: 12px 16px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            font-size: 1rem;
        }
        .filter-btn {
            background-color: #ffffff;
            border: 1px solid var(--border-color);
            padding: 8px 16px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
        }
        .filter-btn.active {
            background-color: var(--primary);
            color: #ffffff;
        }
        code {
            background-color: #f1f5f9;
            color: #e11d48;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Render entire document using this styling scheme based on clean_text content -->
    </div>
    
    <script>
        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.remove('active'));
            const btn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('onclick').includes(tabId));
            if (btn) btn.classList.add('active');
            const panel = document.getElementById(tabId);
            if (panel) panel.classList.add('active');
        }

        function filterSpeaker(speaker) {
            document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
            const activeBtn = document.getElementById('btn-' + speaker);
            if (activeBtn) activeBtn.classList.add('active');
            
            document.querySelectorAll('.dialog-bubble').forEach(bubble => {
                if (speaker === 'all' || bubble.getAttribute('data-speaker') === speaker) {
                    bubble.style.display = 'block';
                } else {
                    bubble.style.display = 'none';
                }
            });
        }

        function filterTranscript() {
            const query = document.getElementById('speakerSearch').value.toLowerCase();
            document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
            const btnAll = document.getElementById('btn-all');
            if (btnAll) btnAll.classList.add('active');

            document.querySelectorAll('.dialog-bubble').forEach(bubble => {
                const text = bubble.querySelector('.dialog-text').innerText.toLowerCase();
                const speaker = bubble.querySelector('.dialog-speaker').innerText.toLowerCase();
                if (text.includes(query) || speaker.includes(query)) {
                    bubble.style.display = 'block';
                } else {
                    bubble.style.display = 'none';
                }
            });
        }
    </script>
</body>
</html>

Generate the complete HTML page matching this clean styling and interactive functionality using this data:
{text}
"""

NAME_PROMPT = """\
Create a short (2-4 words, separated by hyphens, English letters) slug representing the main topic. Only the slug, no quotes or notes.

Text:
{text}
"""


def filter_content_by_time(content: str, time_from: str | None, time_to: str | None) -> str:
    """Filter lines by inline [HH:MM:SS] timestamps."""
    if not time_from and not time_to:
        return content
    lines = content.split('\n')
    filtered = []
    include = True
    for line in lines:
        m = INLINE_TS_RE.match(line)
        if m:
            ts = m.group(1).replace(':', '')  # HHMMSS
            include = (not time_from or ts >= time_from) and (not time_to or ts <= time_to)
        if include:
            filtered.append(line)
    return '\n'.join(filtered).strip()


def collect_files(directory: Path, prefix: str, date_str: str,
                  time_from: str | None, time_to: str | None,
                  deduplicate: bool = False) -> str:
    """Concatenate text files matching date and optional time range."""
    parts = []
    seen: set[str] = set()
    for f in sorted(directory.glob(f"{prefix}_{date_str}_*.txt")):
        name_parts = f.stem.split("_")
        time_part = None
        for i, p in enumerate(name_parts):
            if p == date_str and i + 1 < len(name_parts):
                time_part = name_parts[i + 1][:6]  # HHMMSS
                break

        if time_part and time_to and time_part > time_to:
            continue

        content = f.read_text(encoding="utf-8").strip()
        if not content:
            continue

        content = filter_content_by_time(content, time_from, time_to)
        if not content:
            continue

        if deduplicate:
            if content in seen:
                continue
            seen.add(content)
        parts.append(content)

    return "\n\n".join(parts)


def detect_provider(model: str | None) -> str:
    """Auto-detect provider from model name or available API keys."""
    if model:
        if model.startswith("gemini"):
            return "gemini"
        if model.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
            return "openai"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    return "gemini"


def make_client(provider: str):
    """Create an API client for the given provider."""
    if provider == "gemini":
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print("Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable", file=sys.stderr)
            sys.exit(1)
        return genai.Client(api_key=api_key)
    elif provider == "openai":
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("Set OPENAI_API_KEY environment variable", file=sys.stderr)
            sys.exit(1)
        return OpenAI(api_key=api_key)
    elif provider == "openrouter":
        from openai import OpenAI
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            print("Set OPENROUTER_API_KEY environment variable", file=sys.stderr)
            sys.exit(1)
        return OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    else:
        print(f"Unknown provider: {provider}", file=sys.stderr)
        sys.exit(1)


def generate(client, provider: str, model: str, prompt: str) -> str:
    """Generate text using the given provider."""
    if provider == "gemini":
        response = client.models.generate_content(model=model, contents=prompt)
        return response.text.strip()
    else:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()


def main():
    parser = argparse.ArgumentParser(
        description="Process transcripts through Gemini",
        epilog="Examples:\n"
               "  %(prog)s myproject 2026-06-18\n"
               "  %(prog)s myproject 2026-06-17 --to 2026-06-18\n"
               "  %(prog)s myproject 2026-06-18 --time-from 00:10 --time-to 00:20\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", help="Project name")
    parser.add_argument("date", help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD (optional, inclusive)")
    parser.add_argument("--time-from", dest="time_from", help="Start time HH:MM (optional)")
    parser.add_argument("--time-to", dest="time_to", help="End time HH:MM (optional)")
    parser.add_argument("--model", default=None, help="Model name (default: auto per provider)")
    parser.add_argument("--provider", choices=["gemini", "openai", "openrouter"],
                        default=None, help="API provider (default: auto-detect)")
    args = parser.parse_args()

    project_dir = PROJECTS_DIR / args.project
    transcripts_dir = project_dir / "transcripts"
    screenshots_dir = project_dir / "screenshots"
    docs_dir = project_dir / "docs"

    try:
        date_start = datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"Invalid date format: {args.date}. Use YYYY-MM-DD", file=sys.stderr)
        sys.exit(1)

    date_end = date_start
    if args.date_to:
        try:
            date_end = datetime.strptime(args.date_to, "%Y-%m-%d")
        except ValueError:
            print(f"Invalid date format: {args.date_to}. Use YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)

    from datetime import timedelta
    date_strings = []
    d = date_start
    while d <= date_end:
        date_strings.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    time_from = None
    time_to = None
    if args.time_from:
        time_from = args.time_from.replace(":", "") + "00"
    if args.time_to:
        time_to = args.time_to.replace(":", "") + "59"

    transcript_parts = []
    screenshot_parts_seen: set[str] = set()
    screenshot_parts = []
    for date_str in date_strings:
        tf = time_from if date_str == date_strings[0] else None
        tt = time_to if date_str == date_strings[-1] else None

        t = collect_files(transcripts_dir, "transcript", date_str, tf, tt)
        if t:
            transcript_parts.append(t)

        s = collect_files(screenshots_dir, "screenshot", date_str, tf, tt, deduplicate=True)
        if s:
            for block in s.split("\n\n"):
                if block not in screenshot_parts_seen:
                    screenshot_parts_seen.add(block)
                    screenshot_parts.append(block)

    transcript_text = "\n\n".join(transcript_parts)
    screenshot_text = "\n\n".join(screenshot_parts)

    if not transcript_text:
        date_range = args.date if not args.date_to else f"{args.date} — {args.date_to}"
        print(f"No transcripts found for {date_range}", file=sys.stderr)
        sys.exit(1)

    print(f"Transcript: {len(transcript_text)} chars")
    print(f"Screenshot OCR: {len(screenshot_text)} chars")

    prompt = PROMPT.format(
        transcript=transcript_text,
        screenshots=screenshot_text if screenshot_text else "(no screenshots found)",
    )

    provider = args.provider or detect_provider(args.model)
    model = args.model or PROVIDER_DEFAULTS[provider]
    client = make_client(provider)

    print(f"Sending to {model} ({provider})...")
    clean_text = generate(client, provider, model, prompt)

    print("Generating filename...")
    slug = generate(client, provider, model, NAME_PROMPT.format(text=clean_text[:2000]))
    slug = slug.lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")
    if not slug:
        slug = "transcript"

    docs_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = args.date if not args.date_to else f"{args.date}--{args.date_to}"
    base_name = f"{date_prefix}-{slug}"
    output_path = docs_dir / f"{base_name}.txt"
    output_path.write_text(clean_text, encoding="utf-8")
    print(f"Saved clean markdown: {output_path}")

    print("Generating interactive HTML...")
    html_text = generate(client, provider, model, HTML_PROMPT.replace("{text}", clean_text))
    if html_text.startswith("```"):
        html_text = html_text.split("\n", 1)[1]
    if html_text.endswith("```"):
        html_text = html_text.rsplit("```", 1)[0].rstrip()

    html_path = docs_dir / f"{base_name}.html"
    html_path.write_text(html_text, encoding="utf-8")
    print(f"Saved interactive visual dashboard: {html_path}")


if __name__ == "__main__":
    main()
