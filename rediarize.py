#!/usr/bin/env python3
"""Re-run diarization on a saved session with different parameters."""

import argparse
import pickle
import sys
from datetime import timedelta
from pathlib import Path

from speaker_id import cluster_and_label, diarize_words


def load_session(project_dir, session_name=None):
    """Load session pickle, return (data, session_path)."""
    if session_name:
        session_path = project_dir / session_name
    else:
        sessions = sorted(project_dir.glob("session_*.pkl"))
        if not sessions:
            print(f"No session files found in {project_dir}", file=sys.stderr)
            sys.exit(1)
        session_path = sessions[-1]

    print(f"Loading session: {session_path}")
    with open(session_path, "rb") as f:
        data = pickle.load(f)

    if not data["windows"]:
        print("No embedding windows in session.", file=sys.stderr)
        sys.exit(1)
    if not data["segments"]:
        print("No ASR segments in session.", file=sys.stderr)
        sys.exit(1)

    return data


def sweep(data, lo=0.2, hi=1.0, step=0.05):
    """Try a range of thresholds and show how many speakers each produces."""
    windows = data["windows"]
    print(f"\n{'threshold':>10}  {'speakers':>8}")
    print("-" * 22)
    t = lo
    while t <= hi + 1e-9:
        labels = cluster_and_label(windows, num_speakers=None, threshold=t)
        n = len(set(labels))
        bar = "#" * n
        print(f"  {t:>6.2f}     {n:>3}  {bar}")
        t += step
    print()


def build_diarized_lines(data, num_speakers, cluster_threshold):
    """Run clustering + diarization, return formatted lines."""
    segments = data["segments"]
    windows = data["windows"]
    injections = data["injections"]
    start_time = data["start_time"]

    labels = cluster_and_label(windows, num_speakers, cluster_threshold)
    n_speakers = len(set(labels))
    print(f"Found {n_speakers} speaker(s).")

    # Reset and re-assign speakers
    for seg in segments:
        for word in seg.words:
            word.speaker = 0
    diarize_words(segments, windows, labels)

    all_words = []
    for seg in segments:
        all_words.extend(seg.words)

    if not all_words:
        return []

    # Group consecutive same-speaker words
    items = []
    current_speaker = all_words[0].speaker
    current_words = [all_words[0]]
    current_t = all_words[0].t_start

    for word in all_words[1:]:
        if word.speaker == current_speaker:
            current_words.append(word)
        else:
            text = " ".join(w.text for w in current_words)
            items.append(("speech", current_t, current_speaker, text))
            current_speaker = word.speaker
            current_words = [word]
            current_t = word.t_start

    text = " ".join(w.text for w in current_words)
    items.append(("speech", current_t, current_speaker, text))

    for t_rel, marker in injections:
        items.append(("injection", t_rel, 0, marker))

    items.sort(key=lambda x: x[1])

    lines = []
    for kind, t, speaker, content in items:
        wall = start_time + timedelta(seconds=t)
        time_str = wall.strftime("%H:%M:%S")
        if kind == "speech":
            lines.append(f"[{time_str}] [Speaker {speaker}] {content}")
        else:
            lines.append(f"[{time_str}] {content}")

    return lines


def main():
    parser = argparse.ArgumentParser(
        description="Re-run diarization with different parameters",
        epilog="Examples:\n"
               "  %(prog)s myproject --sweep                    # see threshold→speakers table\n"
               "  %(prog)s myproject --cluster-threshold 0.4    # re-diarize with chosen threshold\n"
               "  %(prog)s myproject --speakers 2               # force 2 speakers\n"
               "  %(prog)s myproject --session session_20260624_143000.pkl --speakers 3\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", help="Project name")
    parser.add_argument("--session", help="Session pickle filename (default: latest)")
    parser.add_argument(
        "--speakers", type=str, default=None,
        help="Number of speakers (e.g. 2) or 'auto' (default: auto)",
    )
    parser.add_argument(
        "--cluster-threshold", type=float, default=0.6,
        help="Cosine distance threshold for auto speaker detection (default: 0.6)",
    )
    parser.add_argument(
        "--sweep", action="store_true",
        help="Show number of speakers for a range of thresholds, then exit",
    )
    args = parser.parse_args()

    project_dir = Path("projects") / args.project
    data = load_session(project_dir, args.session)

    if args.sweep:
        sweep(data)
        return

    # Parse speakers arg
    num_speakers = None
    if args.speakers and args.speakers.lower() != "auto":
        try:
            num_speakers = int(args.speakers)
        except ValueError:
            print(f"--speakers must be a number or 'auto', got: {args.speakers}",
                  file=sys.stderr)
            sys.exit(1)

    print(f"Clustering {len(data['windows'])} embedding windows...")
    lines = build_diarized_lines(data, num_speakers, args.cluster_threshold)

    if not lines:
        print("No words to label.", file=sys.stderr)
        sys.exit(1)

    transcript_path = Path(data["transcript_path"])
    output_path = transcript_path.with_suffix(".diarized.txt")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Diarized transcript: {output_path}")


if __name__ == "__main__":
    main()
