#!/usr/bin/env python3
"""Process raw transcripts + OCR texts through Gemini to produce a clean document."""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from google import genai

TRANSCRIPTS_DIR = Path("transcripts")
SCREENSHOTS_DIR = Path("screenshots")
DOCS_DIR = Path("docs")

MODEL = "gemini-2.5-flash"

PROMPT = """\
Твоя задача — преобразовать СЫРОЙ ТРАНСКРИПТ распознавания речи в чистый, читаемый текст.

ВАЖНО:
- Выведи ТОЛЬКО обработанный транскрипт. Ничего больше.
- НЕ выводи содержимое скриншотов — они даны ТОЛЬКО как справочный материал.
- Не пропускай ничего из содержания транскрипта!
- Убери таймстампы и служебные метки (вроде [screenshot: ...])
- Исправь ошибки распознавания (неправильные слова, склонения, окончания)
- Особое внимание удели идентификаторам, названиям переменных, функций, файлов, технических терминов — они часто распознаются неправильно. Правильные написания ищи в СПРАВОЧНОМ МАТЕРИАЛЕ ниже.
- Сохрани структуру и порядок изложения

## СЫРОЙ ТРАНСКРИПТ (это нужно обработать):

{transcript}

## СПРАВОЧНЫЙ МАТЕРИАЛ — текст со скриншотов экрана (НЕ включай в результат, используй ТОЛЬКО для исправления идентификаторов):

{screenshots}
"""

HTML_PROMPT = """\
Convert the following text into a well-formatted HTML document. Output ONLY the HTML, nothing else.

Requirements:
- Clean, modern styling (system fonts, max-width 800px, centered)
- Use a card-style container with subtle shadow
- Style code/identifier mentions with <code> tags (colored, monospace)
- Use highlight boxes for important deep-dive sections
- Use lists where appropriate
- Keep all content — do not skip anything

Here is an example of the desired output style:

<example_input>
Now, let's see how it looks.
Here is the `provideHttpClient` function implementation because it is an entry point for `HttpClient` configuration. As you can see, it defines a group of providers, and many of whom are already familiar to us. This is the `HttpClient` service itself. This is the `HttpInterceptorHandler` that is used as an implementation of the `HttpHandler` abstraction. And here is the `HttpBackend` token, the implementation of which is resolved inside the factory function, where by default we choose `HttpXhrBackend` implementation, or if you use the `withFetch` function inside your `HttpClient` configuration, then it would provide `FETCH_BACKEND` token, the value of which will be actually the implementation of the `FetchBackend` class. And now let's take a look at the implementation of the `HttpClient`. And as I said, its responsibility is to prepare and normalize the request you want to perform. For example, when you call the `get` method of the `HttpClient`, then internally it passes your configuration to the `request` method, which eventually passes your configuration provided as an object into data structures like `HttpHeaders`, `HttpParams`, and eventually it creates the final `HttpRequest` class instance with the normalized data for the further processing. Once it is done, the request object is passed to the `handle` method of the `HttpHandler`, which is being injected in the `HttpClient` constructor. We will get back to this place and see how the response is handled later on, but now let's follow our request and jump into the `HttpHandler` implementation, which is resolved to `HttpInterceptorHandler` class instance. So here it is. And among other things, it injects `HttpBackend`.
</example_input>

<example_output>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HttpClient Implementation Overview</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 40px auto;
            padding: 0 20px;
            background-color: #f9f9f9;
        }
        .card {
            background: #ffffff;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
            border: 1px solid #e1e4e8;
        }
        h2 {
            color: #2c3e50;
            border-bottom: 2px solid #ecf0f1;
            padding-bottom: 10px;
            margin-top: 0;
        }
        p {
            margin-bottom: 1.2rem;
            font-size: 1.05rem;
        }
        code {
            background-color: #f1f2f6;
            color: #d63031;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
            font-size: 0.9em;
        }
        .highlight-box {
            background-color: #e8f4fd;
            border-left: 4px solid #3498db;
            padding: 15px;
            margin: 20px 0;
            border-radius: 0 4px 4px 0;
        }
    </style>
</head>
<body>

    <div class="card">
        <h2>HttpClient Configuration &amp; Architecture</h2>

        <p>Now, let's see how it looks.</p>

        <p>Here is the <code>provideHttpClient</code> function implementation because it is an entry point for <code>HttpClient</code> configuration. As you can see, it defines a group of providers, many of whom are already familiar to us:</p>

        <ul>
            <li>This is the <code>HttpClient</code> service itself.</li>
            <li>This is the <code>HttpInterceptorHandler</code> that is used as an implementation of the <code>HttpHandler</code> abstraction.</li>
            <li>And here is the <code>HttpBackend</code> token, the implementation of which is resolved inside the factory function. By default, we choose the <code>HttpXhrBackend</code> implementation. However, if you use the <code>withFetch</code> function inside your <code>HttpClient</code> configuration, it will provide the <code>FETCH_BACKEND</code> token, resolving to the implementation of the <code>FetchBackend</code> class.</li>
        </ul>

        <div class="highlight-box">
            <strong>Deep Dive: Inside the HttpClient Implementation</strong>
            <p>As mentioned, its primary responsibility is to prepare and normalize the request you want to perform. For example, when you call the <code>get</code> method of the <code>HttpClient</code>, it internally passes your configuration to the <code>request</code> method.</p>
            <p>This eventually maps your provided configuration object into structured data classes like <code>HttpHeaders</code> and <code>HttpParams</code>, ultimately creating the final <code>HttpRequest</code> instance with normalized data for further processing.</p>
        </div>

        <p>Once this process is complete, the request object is passed to the <code>handle</code> method of the <code>HttpHandler</code>, which is injected directly into the <code>HttpClient</code> constructor.</p>

        <p>We will get back to this place and see how the response is handled later on, but for now, let's follow our request and jump into the <code>HttpHandler</code> implementation. This resolves to the <code>HttpInterceptorHandler</code> class instance. So here it is—and among other things, it injects <code>HttpBackend</code>.</p>
    </div>

</body>
</html>
</example_output>

Now convert this text to HTML following the same style:

{text}
"""

NAME_PROMPT = """\
Придумай короткое (2-4 слова через дефис, латиницей) название-slug для этого текста, \
отражающее его основную тему. Только slug, без кавычек и пояснений.

Текст:
{text}
"""


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

        if time_part and time_from and time_part < time_from:
            continue
        if time_part and time_to and time_part > time_to:
            continue

        content = f.read_text(encoding="utf-8").strip()
        if content:
            if deduplicate:
                if content in seen:
                    continue
                seen.add(content)
            parts.append(content)

    return "\n\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Process transcripts through Gemini",
        epilog="Examples:\n"
               "  %(prog)s 2026-06-18\n"
               "  %(prog)s 2026-06-17 --to 2026-06-18\n"
               "  %(prog)s 2026-06-18 --time-from 00:10 --time-to 00:20\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("date", help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD (optional, inclusive)")
    parser.add_argument("--time-from", dest="time_from", help="Start time HH:MM (optional)")
    parser.add_argument("--time-to", dest="time_to", help="End time HH:MM (optional)")
    parser.add_argument("--model", default=MODEL, help=f"Gemini model (default: {MODEL})")
    args = parser.parse_args()

    # Parse dates
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

    # Build list of date strings
    from datetime import timedelta
    date_strings = []
    d = date_start
    while d <= date_end:
        date_strings.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    # Parse optional time range to HHMMSS format
    time_from = None
    time_to = None
    if args.time_from:
        time_from = args.time_from.replace(":", "") + "00"
    if args.time_to:
        time_to = args.time_to.replace(":", "") + "59"

    # Collect transcripts across all dates
    transcript_parts = []
    screenshot_parts_seen: set[str] = set()
    screenshot_parts = []
    for date_str in date_strings:
        # Time filter only applies to first/last date in range
        tf = time_from if date_str == date_strings[0] else None
        tt = time_to if date_str == date_strings[-1] else None

        t = collect_files(TRANSCRIPTS_DIR, "transcript", date_str, tf, tt)
        if t:
            transcript_parts.append(t)

        s = collect_files(SCREENSHOTS_DIR, "screenshot", date_str, tf, tt, deduplicate=True)
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

    # Build prompt
    prompt = PROMPT.format(
        transcript=transcript_text,
        screenshots=screenshot_text if screenshot_text else "(нет скриншотов)",
    )

    # Call Gemini
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)
    client = genai.Client(api_key=api_key)

    print(f"Sending to {args.model}...")
    response = client.models.generate_content(model=args.model, contents=prompt)
    clean_text = response.text.strip()

    # Ask for a name
    print("Generating filename...")
    name_response = client.models.generate_content(
        model=args.model,
        contents=NAME_PROMPT.format(text=clean_text[:2000]),
    )
    slug = name_response.text.strip().lower().replace(" ", "-")
    # Sanitize slug
    slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")
    if not slug:
        slug = "transcript"

    # Save result
    DOCS_DIR.mkdir(exist_ok=True)
    date_prefix = args.date if not args.date_to else f"{args.date}--{args.date_to}"
    base_name = f"{date_prefix}-{slug}"
    output_path = DOCS_DIR / f"{base_name}.txt"
    output_path.write_text(clean_text, encoding="utf-8")
    print(f"Saved: {output_path}")

    # Generate HTML version
    print("Generating HTML...")
    html_response = client.models.generate_content(
        model=args.model,
        contents=HTML_PROMPT.replace("{text}", clean_text),
    )
    html_text = html_response.text.strip()
    # Strip markdown code fences if present
    if html_text.startswith("```"):
        html_text = html_text.split("\n", 1)[1]
    if html_text.endswith("```"):
        html_text = html_text.rsplit("```", 1)[0].rstrip()

    html_path = DOCS_DIR / f"{base_name}.html"
    html_path.write_text(html_text, encoding="utf-8")
    print(f"Saved: {html_path}")


if __name__ == "__main__":
    main()
