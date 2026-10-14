#!/usr/bin/env python3
"""Compare ASR engines on a recording, the way main.py would see it.

The audio is cut into phrases with the same Silero VAD settings as the live recorder,
then every engine transcribes the same phrases. Each engine runs in its own process
so its load time and peak memory are measured separately.

  python bench_asr.py projects/h1/recording_20261007_083740.mp3
  python bench_asr.py rec.mp3 --start 60 --dur 300 --ref reference.txt
  python bench_asr.py rec.mp3 --engines whisper,gigaam,gigaam-ctc,parakeet

--ref: a hand-corrected transcript of the same --start/--dur range (plain text; lines
like "[12:00:01] [Speaker 1] text" are accepted, the prefixes are stripped). With it,
WER/CER are computed (jiwer). Don't run while the recorder is running — it competes
for the CPU.
"""

import argparse
import json
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
# Same as main.py (not imported: it pulls in easyocr/sounddevice and skews memory)
WHISPER_SIZE, DEVICE, COMPUTE_TYPE, BEAM_SIZE = "large-v3-turbo", "cpu", "int8", 5
VAD_THRESHOLD = 0.5
VAD_CHUNK = 512
MIN_SPEECH_MS = 250

# bench name -> (main.py --engine, --asr-model)
CONFIGS = {
    "whisper": ("whisper", None),
    "gigaam": ("gigaam", None),
    "gigaam-ctc": ("gigaam", "gigaam-v3-e2e-ctc"),
    "parakeet": ("parakeet", None),
}


def load_audio(path: str, start: float, dur: float | None) -> np.ndarray:
    cmd = ["ffmpeg", "-loglevel", "error", "-ss", str(start), "-i", path]
    if dur:
        cmd += ["-t", str(dur)]
    cmd += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout, np.float32)


def vad_segments(audio: np.ndarray, pause_sec: float) -> list[tuple[float, np.ndarray]]:
    """Replicates main.vad_loop: phrases end after pause_sec of silence."""
    import torch
    vad, _ = torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True)
    silence_limit = int(pause_sec * SAMPLE_RATE / VAD_CHUNK)
    min_chunks = int(MIN_SPEECH_MS / 1000 * SAMPLE_RATE / VAD_CHUNK)
    segs, buf, start, silent, speech_chunks = [], [], 0, 0, 0
    for i in range(0, len(audio) - VAD_CHUNK + 1, VAD_CHUNK):
        win = audio[i:i + VAD_CHUNK]
        is_speech = vad(torch.from_numpy(win.copy()), SAMPLE_RATE).item() >= VAD_THRESHOLD
        if is_speech:
            if not buf:
                start = i
            buf.append(win)
            silent = 0
            speech_chunks += 1
        elif buf:
            buf.append(win)
            silent += 1
            if silent >= silence_limit:
                if speech_chunks >= min_chunks:
                    segs.append((start / SAMPLE_RATE, np.concatenate(buf)))
                buf, silent, speech_chunks = [], 0, 0
    if buf and speech_chunks >= min_chunks:
        segs.append((start / SAMPLE_RATE, np.concatenate(buf)))
    return segs


def run_single(cfg: str, seg_file: str, lang: str | None):
    """Child process: load one engine, transcribe all phrases, print JSON."""
    from asr_engines import create_engine
    engine, asr_model = CONFIGS[cfg]
    data = np.load(seg_file)
    starts, lens, audio = data["starts"], data["lens"], data["audio"]
    t = time.time()
    eng = create_engine(engine, device=DEVICE, model_size=WHISPER_SIZE,
                        compute_type=COMPUTE_TYPE, beam_size=BEAM_SIZE, asr_model=asr_model)
    load_sec = time.time() - t
    if engine == "gigaam":
        lang = "ru"
    lines, compute, n_words, pos = [], 0.0, 0, 0
    for st, n in zip(starts, lens):
        seg_audio = audio[pos:pos + n]
        pos += n
        t = time.time()
        segs = list(eng.transcribe(seg_audio, language=lang, word_timestamps=True, hotwords=None))
        compute += time.time() - t
        text = " ".join(s.text.strip() for s in segs if s.text.strip())
        n_words += sum(len(s.words or []) for s in segs)
        lines.append({"t": float(st), "dur": n / SAMPLE_RATE, "text": text})
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)   # macOS: bytes
    print(json.dumps({"label": eng.label, "load_sec": load_sec, "compute_sec": compute,
                      "peak_rss_mb": rss, "timed_words": n_words, "lines": lines}, ensure_ascii=False))


def normalize(text: str) -> str:
    text = re.sub(r"^\s*\[\d\d:\d\d:\d\d\]\s*(\[Speaker \d+\]\s*)?", "", text, flags=re.M)
    text = re.sub(r"^\s*\[(screenshot|selection): .*\]\s*$", "", text, flags=re.M)
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--start", type=float, default=0.0, help="Offset into the recording, seconds")
    ap.add_argument("--dur", type=float, default=None, help="Duration to use, seconds (default: all)")
    ap.add_argument("--engines", default="whisper,gigaam,gigaam-ctc,parakeet")
    ap.add_argument("--lang", default=None, help="Language passed to Whisper (default: auto)")
    ap.add_argument("--pause", type=float, default=1.5, help="Phrase gap, as main.py --pause")
    ap.add_argument("--ref", default=None, help="Reference transcript for WER/CER")
    ap.add_argument("--out", default=None, help="Directory for per-engine transcripts")
    ap.add_argument("--_single", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--_segs", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args._single:
        run_single(args._single, args._segs, args.lang)
        return

    audio = load_audio(args.audio, args.start, args.dur)
    total = len(audio) / SAMPLE_RATE
    print(f"Audio: {total:.1f}s; cutting phrases with Silero VAD (pause {args.pause}s)...", file=sys.stderr)
    segs = vad_segments(audio, args.pause)
    speech = sum(len(a) for _, a in segs) / SAMPLE_RATE
    longest = max((len(a) for _, a in segs), default=0) / SAMPLE_RATE
    print(f"{len(segs)} phrases, {speech:.1f}s of speech, longest {longest:.1f}s", file=sys.stderr)

    out_dir = Path(args.out or f"bench_out/{Path(args.audio).stem}_{int(args.start)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_file = out_dir / "phrases.npz"
    np.savez(seg_file, starts=np.array([s for s, _ in segs]), lens=np.array([len(a) for _, a in segs]),
             audio=np.concatenate([a for _, a in segs]) if segs else np.zeros(0, np.float32))

    ref = normalize(Path(args.ref).read_text(encoding="utf-8")) if args.ref else None
    if ref:
        import jiwer

    rows = []
    for cfg in [e.strip() for e in args.engines.split(",") if e.strip()]:
        if cfg not in CONFIGS:
            print(f"Unknown engine config: {cfg} (choose from {', '.join(CONFIGS)})", file=sys.stderr)
            continue
        print(f"→ {cfg}...", file=sys.stderr)
        cmd = [sys.executable, __file__, args.audio, "--_single", cfg, "--_segs", str(seg_file)]
        if args.lang:
            cmd += ["--lang", args.lang]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"  {cfg} failed:\n{proc.stderr[-1500:]}", file=sys.stderr)
            continue
        res = json.loads(proc.stdout.strip().splitlines()[-1])
        text = "\n".join(f"[{l['t']:7.1f}s] {l['text']}" for l in res["lines"] if l["text"])
        (out_dir / f"{cfg}.txt").write_text(text + "\n", encoding="utf-8")
        hyp = normalize(" ".join(l["text"] for l in res["lines"]))
        empty_phrases = sum(1 for l in res["lines"] if not l["text"])
        row = {"engine": cfg, "label": res["label"], "load": res["load_sec"],
               "rtf": res["compute_sec"] / speech if speech else 0, "compute": res["compute_sec"],
               "rss": res["peak_rss_mb"], "words": len(hyp.split()), "timed": res["timed_words"],
               "empty": empty_phrases}
        if ref:
            row["wer"] = jiwer.wer(ref, hyp)
            row["cer"] = jiwer.cer(ref, hyp)
        rows.append(row)

    print()
    hdr = f"{'engine':<11} {'load s':>7} {'compute s':>9} {'RTF':>6} {'peak MB':>8} {'words':>6} {'timed':>6} {'empty':>6}"
    if ref:
        hdr += f" {'WER':>6} {'CER':>6}"
    print(hdr)
    for r in rows:
        line = (f"{r['engine']:<11} {r['load']:7.1f} {r['compute']:9.1f} {r['rtf']:6.3f} {r['rss']:8.0f} "
                f"{r['words']:6d} {r['timed']:6d} {r['empty']:6d}")
        if ref:
            line += f" {r['wer']:6.3f} {r['cer']:6.3f}"
        print(line)
    print(f"\nTranscripts: {out_dir}/<engine>.txt  (speech {speech:.1f}s in {len(segs)} phrases)")


if __name__ == "__main__":
    main()
