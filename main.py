#!/usr/bin/env python3
"""Real-time Russian speech-to-text using a local ASR model (Whisper, GigaAM or Parakeet)."""

import argparse
import base64
import os
import signal
import subprocess
import sys
import threading
import time
import queue
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
import easyocr

from asr_engines import ENGINES, create_engine, engine_label
import downloads
import media_file
import echo_gate
import native_helpers
from events import events

try:
    from speaker_id import (
        SpeakerEmbedder, WordToken, AsrSegment, EmbeddingWindow, LiveSpeakers,
        cluster_and_label, diarize_words,
    )
    _DIARIZATION_AVAILABLE = True
except ImportError:
    _DIARIZATION_AVAILABLE = False

# ── Config ───────────────────────────────────────────────────────────────────
MODEL_SIZE = "large-v3-turbo"
LANGUAGE = "auto"
DEVICE = "cpu"              # Whisper and the speaker model; --device auto picks cuda when there is one
COMPUTE_TYPE = "int8"       # faster-whisper quantization on cpu (float16 on cuda)
SAMPLE_RATE = 16000
VAD_THRESHOLD = 0.5
SILENCE_DURATION_MS = 1500
MIN_SPEECH_DURATION_MS = 250
SILERO_VAD_REPO = "snakers4/silero-vad:v5.1.2"   # pinned: torch.hub runs the repo's code
APP_CHUNK = 1600            # samples per read from audio_helper (100 ms at 16 kHz)
BEAM_SIZE = 5

# ── Diarization config ──────────────────────────────────────────────────────
PHRASE_GAP_SEC = 0.4       # gap between words to force a phrase split
EMBED_CHUNK_SEC = 3.0      # max phrase duration before forced split (at word boundary)
MIN_PHRASE_AUDIO_SEC = 1.0 # min audio duration for reliable speaker embedding
LINE_SPLIT_SEC = 30.0      # a longer stretch of speech without a pause becomes several transcript lines
SENTENCE_END_RE = re.compile(r"[.!?…][»\"\')\]]*$")

# ── Globals ──────────────────────────────────────────────────────────────────
audio_queue: queue.Queue[np.ndarray] = queue.Queue()   # main channel (mic, app or file); None = end of input
app_queue: queue.Queue[np.ndarray] = queue.Queue()     # second channel in app+mic mode (the app)
transcribe_queue: queue.Queue = queue.Queue()      # items: (t_start, audio, label)
embed_queue: queue.Queue = queue.Queue()            # items: (t_start, t_end, audio)
stop_event = threading.Event()
screenshot_dir: Path | None = None
ocr_queue: queue.Queue[str] = queue.Queue()
frame_ocr_queue: queue.Queue = queue.Queue()   # (path, frame id): after the screenshots
pending_injections: queue.Queue = queue.Queue()     # marker, or (t_relative, marker) for a known time
audio_chunks: list[np.ndarray] = []
app_chunks: list[np.ndarray] = []     # app channel in app+mic mode (mixed in when saving audio)
save_audio_flag = False
mic_muted = threading.Event()   # set via --control-stdin MUTE/UNMUTE
cut_requests = 0                # bumped by --control-stdin CUT: end the current phrase now
hotkey_snap = None   # video file: Ctrl+Shift+S grabs a frame of the video instead of the screen
live_frames = None   # autoframes.LiveFrames once auto frames are switched on (--auto-frames / AUTO ON)
channel_rms: dict[str, float] = {}    # last level per channel; the meter shows the loudest
file_pos = 0.0          # file source: seconds of the range fed to the VAD so far
vad_pos = 0.0           # file source: seconds of the range the VAD has gone through
session_t0 = 0.0                      # time.monotonic() at session_data["start_time"]
channel_start: dict[str, float] = {}  # "mic" / "app": session time of the channel's first sample
app_loudness: echo_gate.AppLoudness | None = None   # app+mic mode: to drop the app's echo in the mic


def load_models(engine_name="whisper", asr_model=None):
    """Load the ASR engine and Silero VAD."""
    if engine_name == "whisper":
        size = asr_model or MODEL_SIZE
        print(f"Loading Whisper model '{size}' (device={DEVICE}, compute={COMPUTE_TYPE})...")
        events.emit("status", state="loading", detail=f"Loading Whisper {size}")
    else:
        label = engine_label(engine_name, asr_model)
        print(f"Loading {label} (device=cpu)... (first run downloads the model)")
        events.emit("status", state="loading", detail=f"Loading {label}")
    asr_engine = create_engine(engine_name, device=DEVICE, model_size=MODEL_SIZE,
                               compute_type=COMPUTE_TYPE, beam_size=BEAM_SIZE, asr_model=asr_model)
    if DEVICE == "cuda" and engine_name == "whisper":
        asr_engine = _cuda_or_cpu(asr_engine, engine_name, asr_model)
    print("Whisper model loaded." if engine_name == "whisper" else f"{asr_engine.label} loaded.")

    print("Loading Silero VAD...")
    events.emit("status", state="loading", detail="Loading Silero VAD")
    vad_model = load_vad()
    print("Silero VAD loaded.")
    return asr_engine, vad_model


def _cuda_or_cpu(asr_engine, engine_name, asr_model):
    """The CUDA libraries (cuBLAS, cuDNN) load on the first transcription, not with the
    model: try one second of silence now and fall back to the CPU if they are missing."""
    global DEVICE, COMPUTE_TYPE
    try:
        list(asr_engine.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en",
                                   word_timestamps=False, hotwords=None))
        return asr_engine
    except Exception as e:
        print(f"[cuda] {e}; using the CPU", file=sys.stderr)
        events.emit("error", where="GPU", message=f"CUDA is not usable ({e}); recognizing on the CPU")
    DEVICE, COMPUTE_TYPE = "cpu", "int8"
    return create_engine(engine_name, device=DEVICE, model_size=MODEL_SIZE,
                         compute_type=COMPUTE_TYPE, beam_size=BEAM_SIZE, asr_model=asr_model)


def load_vad():
    silero_dir = os.environ.get("VOICE_REC_SILERO_DIR")   # bundled copy (macOS app)
    if silero_dir and Path(silero_dir, "hubconf.py").exists():
        vad_model, utils = torch.hub.load(repo_or_dir=silero_dir, model="silero_vad", source="local")
    else:
        try:
            from silero_vad import load_silero_vad     # pip install silero-vad: no remote code at all
            return load_silero_vad()
        except ImportError:
            pass
        vad_model, utils = torch.hub.load(
            repo_or_dir=SILERO_VAD_REPO,
            model="silero_vad",
            trust_repo=True,
        )
    return vad_model


def audio_callback(indata, frames, time_info, status):
    """sounddevice callback - push mono float32 frames into audio_queue."""
    if status:
        print(f"[audio] {status}", file=sys.stderr)
    data = indata[:, 0].copy()
    mark_channel_start("mic", data)
    if mic_muted.is_set():
        # Feed silence (not nothing) so timestamps stay aligned with the wall clock
        # and a phrase in progress ends normally.
        data = np.zeros_like(data)
    audio_queue.put(data)
    emit_level("mic", data)
    if save_audio_flag:
        audio_chunks.append(data)


def mark_channel_start(channel: str, data: np.ndarray):
    """Remember when a live channel's first sample was heard: the models load and the helpers
    start after start_time, so a channel's sample count alone runs behind the wall clock."""
    if channel not in channel_start:
        channel_start[channel] = time.monotonic() - session_t0 - len(data) / SAMPLE_RATE


def emit_level(channel: str, data: np.ndarray):
    if events.enabled:
        channel_rms[channel] = float(np.sqrt(np.mean(data * data))) if len(data) else 0.0
        events.emit_throttled("level", 0.08, "level", rms=max(channel_rms.values()))


def app_audio_reader(proc, target_queue, chunks):
    """Read 16 kHz mono float32 from audio_helper (paced by the wall clock, zeros when the
    app is silent) and feed it to a VAD channel like the mic callback does."""
    nbytes = APP_CHUNK * 4
    while True:
        raw = proc.stdout.read(nbytes)
        if not raw or len(raw) < 4:
            break
        data = np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32).copy()
        mark_channel_start("app", data)
        if mic_muted.is_set():
            data = np.zeros_like(data)
        if app_loudness is not None:
            app_loudness.add(data, channel_start["app"])
        target_queue.put(data)
        emit_level("app", data)
        if save_audio_flag:
            chunks.append(data)
    if not stop_event.is_set():
        print(f"\r\033[K[app audio] helper exited (code {proc.poll()})", file=sys.stderr)
        events.emit("error", where="app audio", message="The app audio capture stopped")


def helper_log_reader(proc, prefix):
    for line in proc.stderr:
        line = line.decode("utf-8", "replace").strip() if isinstance(line, bytes) else line.strip()
        if line:
            print(f"\r\033[K{prefix} {line}")
            if "zeros" in line or "error" in line or "warning:" in line:
                events.emit("error", where=prefix.strip("[]"), message=line.removeprefix("[audio_helper] "))


def decode_audio_file(path: Path, work_dir: Path, start: float = 0.0, end: float | None = None) -> np.ndarray:
    """Any audio or video (mp3, m4a, wav, mp4, mov, ...) → 16 kHz mono float32 of [start, end),
    via ffmpeg (afconvert as fallback, audio only)."""
    tmp = work_dir / f".decode_{os.getpid()}.raw"
    try:
        try:
            ffmpeg = native_helpers.ffmpeg_exe()
            if not ffmpeg:
                if not native_helpers.IS_MAC:
                    raise RuntimeError("ffmpeg not found: install it (https://ffmpeg.org) or pip install imageio-ffmpeg")
                raise FileNotFoundError("ffmpeg")
            r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", *media_file.range_args(start, end), "-i", str(path),
                                "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", str(tmp)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or f"ffmpeg exited with {r.returncode}")
            return np.fromfile(tmp, dtype="<f4")
        except FileNotFoundError:     # no ffmpeg: macOS's own converter (mp3, m4a, aac, wav, aiff, flac)
            r = subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEF32@{SAMPLE_RATE}", "-c", "1",
                                str(path), str(tmp)], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or f"afconvert exited with {r.returncode}")
            data = tmp.read_bytes()
            pos = 12                  # RIFF header; find the "data" chunk
            while pos + 8 <= len(data):
                cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
                if cid == b"data":
                    audio = np.frombuffer(data[pos + 8:pos + 8 + size], dtype="<f4")
                    return audio[int(start * SAMPLE_RATE):None if end is None else int(end * SAMPLE_RATE)].copy()
                pos += 8 + size + (size & 1)
            raise RuntimeError("afconvert produced no audio data")
    finally:
        tmp.unlink(missing_ok=True)


def file_feeder(audio: np.ndarray, realtime: bool = False, offset: float = 0.0):
    """Feed a decoded file into the VAD, then end the input. As fast as the pipeline takes it,
    or (realtime) at playback speed, so a player can run alongside. MUTE holds it (pause).
    *offset* = where the range starts in the file (progress reports file positions)."""
    global file_pos
    total = len(audio) / SAMPLE_RATE
    clock = time.monotonic()        # realtime: when sample 0 is due (moves on after a hold)
    held = False

    def progress(done, force=False):
        data = dict(done=round(done, 2), total=round(total, 2), offset=round(offset, 2),
                    pos=round(offset + done, 2), paused=held)
        if force:
            events.emit("source_progress", **data)
        else:
            events.emit_throttled("source_progress", 0.25 if realtime else 0.5, "source_progress", **data)

    for i in range(0, len(audio), APP_CHUNK):
        if stop_event.is_set():
            break
        if mic_muted.is_set():
            held = True
            progress(i / SAMPLE_RATE, force=True)
            while mic_muted.is_set() and not stop_event.is_set():
                stop_event.wait(0.05)
            held = False
            clock = time.monotonic() - i / SAMPLE_RATE
            progress(i / SAMPLE_RATE, force=True)
            if stop_event.is_set():
                break
        if realtime:
            delay = clock + i / SAMPLE_RATE - time.monotonic()
            if delay > 0 and stop_event.wait(delay):
                break
        else:
            # Stay a little ahead of the VAD, and don't run far ahead of the recognizer either.
            while (audio_queue.qsize() > 50 or transcribe_queue.qsize() > 3) and not stop_event.is_set():
                stop_event.wait(0.05)
        chunk = audio[i:i + APP_CHUNK]
        audio_queue.put(chunk)
        file_pos = min(total, (i + APP_CHUNK) / SAMPLE_RATE)
        emit_level("file", chunk)
        progress(file_pos)
    if not stop_event.is_set():
        file_pos = total
    progress(file_pos, force=True)
    audio_queue.put(None)


def video_frames_worker(path: Path, start: float, end: float | None, interval: float, halt: threading.Event,
                        begin: float | None = None):
    """Auto frames for a video file: frames straight from the video (full resolution) every
    *interval* seconds, paced by the audio (never ahead of what the VAD has been fed), into
    live_frames. Starts at *begin* (seconds into the range; default: where the audio is now,
    so AUTO OFF / ON resumes there)."""
    begin = file_pos if begin is None else begin
    if live_frames is not None:
        live_frames.wait_ready()
    tmp_dir = screenshot_dir / ".frames"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        reader = media_file.FrameReader(path, start + begin, end, interval)
    except Exception as e:
        print(f"[frames] {e}", file=sys.stderr)
        events.emit("error", where="frames", message=str(e))
        return
    try:
        for t, jpeg in reader:
            t += begin
            # Pace: the frame's moment must have been reached by the audio; don't flood triage.
            # After the end / a stop, frames up to where the audio got are still taken.
            past_end = False
            while not halt.is_set():
                if t > file_pos + 0.5:
                    if stop_event.is_set():
                        past_end = True
                        break
                elif live_frames is None or live_frames.q.qsize() <= 4:
                    break
                halt.wait(0.05)
            if past_end or halt.is_set() or live_frames is None:
                break
            out = tmp_dir / f"frame_{t:010.3f}.jpg"
            out.write_bytes(jpeg)
            live_frames.put(str(out), t=t)
        else:       # ran to the end of the range: anything ffmpeg said is worth showing
            if reader.error:
                print(f"[frames] ffmpeg: {reader.error}", file=sys.stderr)
    finally:
        reader.close()


def video_snap(path: Path, start: float, at: float | None = None):
    """Screenshot of a video file: the frame at *at* (file position; default: where the audio
    is now), lossless PNG, put into the transcript at that position."""
    at = start + file_pos if at is None else at
    # Named by the position in the file (like the transcript's times), not by the clock
    stamp = datetime.combine(datetime.now().date(), datetime.min.time()) + timedelta(seconds=at)
    out = screenshot_dir / f"screenshot_{stamp:%Y%m%d_%H%M%S_%f}"[:-3]
    out = out.with_name(out.name + ".png")
    try:
        media_file.grab_frame(path, at, out)
    except Exception as e:
        print(f"[snap] {e}", file=sys.stderr)
        events.emit("error", where="snap", message=str(e))
        return
    path_s = str(out.resolve())
    pending_injections.put((max(0.0, at - start), f"[screenshot: {path_s}]"))
    ocr_queue.put(path_s)
    events.emit("screenshot", path=path_s)


def flush_pending_injections(output_file, session_data):
    """Write pending injection markers (screenshots, selections) to transcript."""
    while True:
        try:
            item = pending_injections.get_nowait()
        except queue.Empty:
            break
        if isinstance(item, tuple):     # known position (a frame of a video file)
            t_rel, marker = item
            wall_time = (session_data["start_time"] + timedelta(seconds=t_rel)).strftime("%H:%M:%S")
        else:
            marker = item
            t_rel = (datetime.now() - session_data["start_time"]).total_seconds()
            wall_time = datetime.now().strftime("%H:%M:%S")
        sys.stdout.write(f"\r\033[K[{wall_time}] {marker}\n")
        sys.stdout.flush()
        output_file.write(f"[{wall_time}] {marker}\n")
        output_file.flush()
        session_data["injections"].append((t_rel, marker))
        if marker.startswith("[screenshot: "):
            events.emit("injection", kind="screenshot", time=wall_time,
                        path=marker[len("[screenshot: "):-1])
        elif marker.startswith("[selection: "):
            events.emit("injection", kind="selection", time=wall_time,
                        text=marker[len("[selection: "):-1])


def detect_language(words, threshold=0.8):
    """Return 'ru' or 'en' based on Cyrillic ratio, or None if uncertain."""
    meaningful = [w for w in words if len(w) > 1 and any(c.isalpha() for c in w)]
    if len(meaningful) < 10:
        return None
    cyrillic = sum(1 for w in meaningful if any("\u0400" <= c <= "\u04ff" for c in w))
    ratio = cyrillic / len(meaningful)
    if ratio >= threshold:
        return "ru"
    if ratio <= 1 - threshold:
        return "en"
    return None


# ── Vocabulary hints (--vocab-file) ────────────────────────────────────────
VOCAB_RE = re.compile(r'^\s*[-*]\s*"(.+?)"\s*(?:→|->)\s*"(.+?)"')
MAX_HOTWORDS_CHARS = 400
vocab_path: Path | None = None
_vocab_cache = {"mtime": None, "hotwords": None}


def current_hotwords() -> str | None:
    """Correct spellings from '- "heard" → "meant"' lines in the vocab file,
    re-read whenever the file changes. Passed to Whisper as hotwords."""
    if vocab_path is None:
        return None
    try:
        mtime = vocab_path.stat().st_mtime
    except OSError:
        return None
    if mtime != _vocab_cache["mtime"]:
        terms = []
        for line in vocab_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = VOCAB_RE.match(line)
            if m and m.group(2) not in terms:
                terms.append(m.group(2))
        hw = ", ".join(terms)[:MAX_HOTWORDS_CHARS].rsplit(",", 1)[0] if terms else ""
        _vocab_cache.update(mtime=mtime, hotwords=hw or None)
        if hw:
            sys.stdout.write(f"\r\033[K[vocab] {len(terms)} hint(s) loaded\n")
            sys.stdout.flush()
    return _vocab_cache["hotwords"]


# ── Vocabulary corrections (--vocab-correct) ─────────────────────────────────
# For engines without hotwords: replace '"heard" → "meant"' phrases in the output.
vocab_correct = False
_corr_cache = {"mtime": None, "rules": []}


def current_corrections() -> list:
    """[(compiled regex, replacement)] from the vocab file, re-read on change."""
    if vocab_path is None:
        return []
    try:
        mtime = vocab_path.stat().st_mtime
    except OSError:
        return []
    if mtime != _corr_cache["mtime"]:
        rules = []
        for line in vocab_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = VOCAB_RE.match(line)
            if m and m.group(1).strip() and m.group(1) != m.group(2):
                rules.append((re.compile(r"(?<!\w)" + re.escape(m.group(1).strip()) + r"(?!\w)",
                                         re.IGNORECASE), m.group(2)))
        _corr_cache.update(mtime=mtime, rules=rules)
    return _corr_cache["rules"]


def apply_corrections(text: str) -> str:
    for rx, meant in current_corrections():
        text = rx.sub(meant, text)
    return text


# ── Language detection config ──────────────────────────────────────────────
LANG_FIRST_CHECK = 30      # words before first detection
LANG_RECHECK_INTERVAL = 200 # words between periodic re-checks
LANG_WINDOW = 300           # sliding window size for re-checks


def transcription_worker(asr_engine, output_file, session_data, enable_diarization, lang_mode,
                         live_speakers=None):
    """Daemon thread: transcribe speech segments and stream results to console.
    live_speakers (diarization on): numbers each line's speaker right away."""
    # Language state
    if lang_mode == "auto":
        current_lang = None  # Whisper auto-detects per segment
        lang_words = []
        next_lang_check = LANG_FIRST_CHECK
    else:
        current_lang = lang_mode

    while True:
        try:
            item = transcribe_queue.get(timeout=0.5)
        except queue.Empty:
            flush_pending_injections(output_file, session_data)
            continue

        if item is None:  # sentinel → exit
            transcribe_queue.task_done()
            break

        seg_t_start, segment_audio, label = item
        tag = f"[{label}] " if label else ""
        # app+mic mode: only the app channel ("Them") goes to diarization; "Me" is known
        diarize_this = enable_diarization and label != "Me"
        duration = len(segment_audio) / SAMPLE_RATE
        sys.stdout.write(f"\r\033[K[transcribing {duration:.1f}s...] ")
        sys.stdout.flush()
        events.emit("transcribing", t=round(seg_t_start, 2), duration=round(duration, 1),
                    queued=transcribe_queue.qsize())

        try:
            segments = asr_engine.transcribe(
                segment_audio,
                language=current_lang,
                word_timestamps=diarize_this,
                hotwords=current_hotwords() if asr_engine.supports_hotwords else None,
            )
            correct = vocab_correct and vocab_path is not None

            def stamp(t_rel: float) -> str:
                # Wall-clock timestamp derived from sample-accurate time
                return (session_data["start_time"] + timedelta(seconds=seg_t_start + t_rel)).strftime("%H:%M:%S")

            def line_speaker():
                """The line's speaker number while recording (diarization on), else the channel label."""
                if live_speakers is None or not diarize_this:
                    return label
                # Only the words' span: the segment ends with the pause, and silence blurs the voice
                # (and would make a one-word phrase look long enough to start a new speaker)
                words = [w for w in asr_words if w.t_start >= seg_t_start + line_t - 0.01]
                s, e = ((words[0].t_start - seg_t_start, words[-1].t_end - seg_t_start) if words else
                        (line_t, seg_t_end - (SILENCE_DURATION_MS / 1000 if seg_t_end == duration else 0)))
                s, e = int(max(0.0, s) * SAMPLE_RATE), int(e * SAMPLE_RATE)
                audio = (segment_audio[s:e] if e - s >= SAMPLE_RATE // 2
                         else segment_audio[int(line_t * SAMPLE_RATE):int(seg_t_end * SAMPLE_RATE)])
                try:
                    return live_speakers.label(audio, max(0, e - s) / SAMPLE_RATE) or label
                except Exception as ex:
                    print(f"\r\033[K[speakers] live label failed: {ex}", file=sys.stderr)
                    return label

            def end_line():
                output_file.write("\n")
                output_file.flush()
                events.emit("segment", time=stamp(line_t), text=" ".join(line_texts),
                            t=round(seg_t_start + line_t, 2), duration=round(seg_t_end - line_t, 1),
                            speaker=line_speaker())
                if enable_diarization and not diarize_this:
                    session_data["fixed"].append((seg_t_start + line_t, label, " ".join(line_texts)))

            first = True
            has_text = False
            asr_words = []
            seg_texts = []
            line_t = 0.0        # start of the current transcript line, relative to the segment
            line_texts = []

            for seg in segments:
                text = seg.text.strip()
                if correct and text:
                    text = apply_corrections(text)
                if text:
                    # Minutes of speech without a pause would be one line with one timestamp;
                    # start a new line at an ASR chunk that begins a sentence, so the text
                    # keeps its timing (frames and screenshots land next to the right words).
                    if not first and seg.start - line_t >= LINE_SPLIT_SEC and SENTENCE_END_RE.search(line_texts[-1]):
                        seg_t_end = seg.start
                        end_line()
                        sys.stdout.write("\n")
                        line_t, line_texts, first = seg.start, [], True
                    if first:
                        sys.stdout.write(f"\r\033[K[{stamp(line_t)}] {tag}")
                        output_file.write(f"[{stamp(line_t)}] {tag}")
                        first = False
                    sys.stdout.write(text + " ")
                    sys.stdout.flush()
                    output_file.write(text + " ")
                    has_text = True
                    seg_texts.append(text)
                    line_texts.append(text)
                if events.enabled:
                    events.emit("transcribe_progress", t=round(seg_t_start, 2),
                                done=round(min(seg.end, duration), 1), duration=round(duration, 1),
                                text=text)

                # Collect word-level data for diarization
                if diarize_this and seg.words:
                    for w in seg.words:
                        word_text = w.word.strip()
                        if correct and word_text:
                            word_text = apply_corrections(word_text)
                        if word_text:
                            asr_words.append(WordToken(
                                text=word_text,
                                t_start=seg_t_start + w.start,
                                t_end=seg_t_start + w.end,
                            ))

            if has_text:
                sys.stdout.write("\n")
                sys.stdout.flush()
                seg_t_end = duration
                end_line()
                if diarize_this and asr_words:
                    session_data["segments"].append(AsrSegment(
                        t_start=seg_t_start,
                        t_end=seg_t_start + duration,
                        words=asr_words,
                    ))

                    # Group words into phrases: split on gaps OR max duration
                    phrases = [[asr_words[0]]]
                    for w in asr_words[1:]:
                        prev = phrases[-1][-1]
                        phrase_dur = w.t_end - phrases[-1][0].t_start
                        gap = w.t_start - prev.t_end
                        if gap > PHRASE_GAP_SEC or phrase_dur > EMBED_CHUNK_SEC:
                            phrases.append([w])
                        else:
                            phrases[-1].append(w)

                    for phrase in phrases:
                        abs_start = phrase[0].t_start
                        abs_end = phrase[-1].t_end
                        rel_start = abs_start - seg_t_start
                        rel_end = abs_end - seg_t_start
                        # Pad short phrases for better embedding quality
                        phrase_dur = rel_end - rel_start
                        if phrase_dur < MIN_PHRASE_AUDIO_SEC:
                            pad = (MIN_PHRASE_AUDIO_SEC - phrase_dur) / 2
                            rel_start = max(0, rel_start - pad)
                            rel_end = min(duration, rel_end + pad)
                        s = int(rel_start * SAMPLE_RATE)
                        e = int(rel_end * SAMPLE_RATE)
                        if e > s:
                            embed_queue.put((abs_start, abs_end, segment_audio[s:e].copy()))
            else:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
                events.emit("segment_empty")

            # Language auto-detection
            if lang_mode == "auto" and seg_texts:
                for t in seg_texts:
                    lang_words.extend(t.split())
                if len(lang_words) >= next_lang_check:
                    # Use sliding window for re-checks
                    check_words = lang_words[-LANG_WINDOW:]
                    new_lang = detect_language(check_words)
                    if new_lang and new_lang != current_lang:
                        current_lang = new_lang
                        sys.stdout.write(f"\r\033[K[lang] detected: {current_lang}\n")
                        sys.stdout.flush()
                        events.emit("lang", lang=current_lang)
                    next_lang_check = len(lang_words) + LANG_RECHECK_INTERVAL

        except Exception as e:
            print(f"\r\033[K[transcription error] {e}", file=sys.stderr)
            events.emit("error", where="transcription", message=str(e))
        finally:
            transcribe_queue.task_done()
            events.emit("chunk_done", t=round(seg_t_start, 2), queued=transcribe_queue.qsize())
        flush_pending_injections(output_file, session_data)


def embed_worker(embedder, session_data):
    """Daemon thread: compute speaker embeddings for phrase audio.
    Exits when it receives None sentinel from the queue."""
    count = 0
    while True:
        item = embed_queue.get()
        if item is None:
            embed_queue.task_done()
            break
        t_start, t_end, audio = item
        count += 1
        try:
            vector = embedder.embed(audio)
            session_data["windows"].append(EmbeddingWindow(
                vector=vector, t_start=t_start, t_end=t_end,
            ))
            sys.stdout.write(f"\r\033[K[embedding] {count} phrases")
            sys.stdout.flush()
            events.emit_throttled("embedding", 1.0, "embedding", count=count)
        except Exception as e:
            print(f"[embed error] {e}", file=sys.stderr)
        finally:
            embed_queue.task_done()
    if count:
        sys.stdout.write(f"\r\033[K[embedding] done ({count} phrases)\n")
        sys.stdout.flush()


def vad_loop(vad_model, source=audio_queue, label=None, channel=None):
    """VAD loop over one channel: detect speech segments and enqueue them for transcription.
    label marks the channel's lines in app+mic mode ("Me" / "Them"); None otherwise.
    channel ("mic" / "app") places its phrases on the session clock; None for an audio file.
    Runs until stop_event, or until the source yields None (end of an audio file)."""
    global vad_pos
    vad_chunk_samples = 512  # 32ms at 16kHz
    silence_chunks = int((SILENCE_DURATION_MS / 1000) * SAMPLE_RATE / vad_chunk_samples)
    min_speech_chunks = int((MIN_SPEECH_DURATION_MS / 1000) * SAMPLE_RATE / vad_chunk_samples)

    buffer = np.array([], dtype=np.float32)
    speech_buffer = np.array([], dtype=np.float32)
    is_speaking = False
    silent_count = 0
    speech_count = 0

    # Sample-accurate timing
    samples_processed = 0
    speech_start_sample = 0
    cut_seen = cut_requests

    while not stop_event.is_set():
        # Drain audio_queue into buffer
        try:
            chunk = source.get(timeout=0.1)
        except queue.Empty:
            continue
        if chunk is None:
            break
        buffer = np.concatenate([buffer, chunk])

        # Process all complete VAD windows
        while len(buffer) >= vad_chunk_samples:
            window = buffer[:vad_chunk_samples]
            buffer = buffer[vad_chunk_samples:]

            tensor = torch.from_numpy(window)
            speech_prob = vad_model(tensor, SAMPLE_RATE).item()
            is_speech = speech_prob >= VAD_THRESHOLD

            samples_processed += vad_chunk_samples
            if source is audio_queue:
                vad_pos = samples_processed / SAMPLE_RATE

            if is_speech:
                if not is_speaking:
                    is_speaking = True
                    silent_count = 0
                    speech_count = 0
                    speech_start_sample = samples_processed - vad_chunk_samples
                    events.emit("speech_start")
                speech_count += 1
                silent_count = 0
                speech_buffer = np.concatenate([speech_buffer, window])
                # Show recording status with duration
                duration = len(speech_buffer) / SAMPLE_RATE
                sys.stdout.write(f"\r\033[K● speech [{duration:.1f}s]")
                sys.stdout.flush()
                events.emit_throttled("speech", 0.25, "speech", duration=round(duration, 1))
            elif is_speaking:
                silent_count += 1
                speech_buffer = np.concatenate([speech_buffer, window])

            # Re-read each window so the pause can be changed while recording
            silence_chunks = int((SILENCE_DURATION_MS / 1000) * SAMPLE_RATE / vad_chunk_samples)
            # CUT ends the phrase right away instead of waiting for the pause
            cut = cut_requests != cut_seen
            cut_seen = cut_requests
            if is_speaking and (cut or silent_count >= silence_chunks):
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
                # End of speech segment
                queued = speech_count >= min_speech_chunks
                t_start = channel_start.get(channel, 0.0) + speech_start_sample / SAMPLE_RATE
                echo = False
                if queued and label == "Me" and app_loudness is not None:
                    echo, corr = app_loudness.is_echo(speech_buffer, t_start)
                    if echo:
                        queued = False
                        print(f"\r\033[K[echo] dropped {len(speech_buffer) / SAMPLE_RATE:.1f}s mic phrase: "
                              f"the other side through the speakers (r={corr:.2f})")
                if queued:
                    transcribe_queue.put((t_start, speech_buffer.copy(), label))
                events.emit("speech_end", queued=queued, t=round(t_start, 2),
                            duration=round(len(speech_buffer) / SAMPLE_RATE, 1), cut=cut, echo=echo)
                speech_buffer = np.array([], dtype=np.float32)
                is_speaking = False
                silent_count = 0
                speech_count = 0

    # Flush remaining speech buffer on shutdown
    if len(speech_buffer) > 0 and speech_count >= min_speech_chunks:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        t_start = channel_start.get(channel, 0.0) + speech_start_sample / SAMPLE_RATE
        transcribe_queue.put((t_start, speech_buffer.copy(), label))
        events.emit("speech_end", queued=True, t=round(t_start, 2),
                    duration=round(len(speech_buffer) / SAMPLE_RATE, 1))


def finalize_diarization(session_data, output_path, num_speakers, cluster_threshold,
                         raw_text=None, prefix_text=""):
    """Post-process: cluster embeddings, label words by speaker, rewrite transcript.

    raw_text: if given, written to .raw.txt (instead of renaming the provisional file).
    prefix_text: kept verbatim before the diarized lines (earlier part of a continued
    session that has no speaker data)."""
    segments = session_data["segments"]
    windows = session_data["windows"]
    injections = session_data["injections"]
    start_time = session_data["start_time"]

    if not windows:
        print("[diarization] No embedding windows collected, skipping.")
        return
    if not segments:
        print("[diarization] No ASR segments collected, skipping.")
        return

    print(f"[diarization] Clustering {len(windows)} embedding windows...")
    events.emit("diarization", state="clustering", windows=len(windows))
    labels = cluster_and_label(windows, num_speakers, cluster_threshold)

    n_speakers = len(set(labels))
    print(f"[diarization] Found {n_speakers} speaker(s).")
    events.emit("diarization", state="labeling", speakers=n_speakers)

    # Assign speaker labels to every word
    diarize_words(segments, windows, labels)

    # Collect all words in order
    all_words = []
    for seg in segments:
        all_words.extend(seg.words)

    if not all_words:
        print("[diarization] No words to label.")
        return

    # Group consecutive same-speaker words into items
    items = []  # (kind, time, speaker_or_0, text)
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

    # Lines whose speaker is known without clustering (app+mic mode: "Me")
    for t_rel, label, content in session_data.get("fixed", []):
        items.append(("speech", t_rel, label, content))

    # Add injection markers (screenshots, selections)
    for t_rel, marker in injections:
        items.append(("injection", t_rel, 0, marker))

    # Sort everything by time
    items.sort(key=lambda x: x[1])

    # Build output lines
    lines = []
    for kind, t, speaker, content in items:
        wall = start_time + timedelta(seconds=t)
        time_str = wall.strftime("%H:%M:%S")
        if kind == "speech":
            lines.append(f"[{time_str}] [{speaker}] {content}" if isinstance(speaker, str)
                         else f"[{time_str}] [Speaker {speaker}] {content}")
        else:
            lines.append(f"[{time_str}] {content}")

    # Keep provisional as .raw.txt, write diarized to original path
    raw_path = output_path.with_suffix(".raw.txt")
    if raw_text is None:
        output_path.replace(raw_path)
    else:
        raw_path.write_text(raw_text, encoding="utf-8")
    output_path.write_text(prefix_text + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"[diarization] Raw transcript kept: {raw_path}")
    print(f"[diarization] Diarized transcript: {output_path}")
    events.emit("diarization", state="done", speakers=n_speakers, path=str(output_path))


def _cpu_lacks_avx2() -> bool:
    """x86_64 without AVX2 (e.g. the Intel build under Rosetta): PyTorch's quantized kernels
    (fbgemm) would crash with an illegal instruction."""
    import platform
    if platform.machine() != "x86_64" or not native_helpers.IS_MAC:
        return False
    try:
        out = subprocess.run(["sysctl", "-n", "hw.optional.avx2_0"], capture_output=True, text=True).stdout
        return out.strip() != "1"
    except OSError:
        return True


def ocr_worker():
    """Daemon thread: OCR screenshots and auto frames, save the text next to each image (.txt).
    Screenshots go first; frames when there is no screenshot waiting. On the sentinel the
    frames still waiting are done too, so every frame of the session has its text."""
    try:
        with downloads.watch_easyocr():
            reader = easyocr.Reader(["ru", "en"], gpu=False, verbose=False, quantize=not _cpu_lacks_avx2())
    except Exception as e:
        print(f"[OCR] disabled, model failed to load: {e}", file=sys.stderr)
        events.emit("error", where="OCR", message=f"screenshot text recognition is off: {downloads._explain(e)}")
        reader = None
    stopping = False
    while True:
        frame_id = None
        try:
            q, image_path = ocr_queue, ocr_queue.get(timeout=0 if stopping or not frame_ocr_queue.empty() else 0.5)
        except queue.Empty:
            try:
                q, (image_path, frame_id) = frame_ocr_queue, frame_ocr_queue.get_nowait()
            except queue.Empty:
                if stopping:
                    ocr_queue.task_done()    # the sentinel's
                    break
                continue

        if image_path is None:  # sentinel → finish the frames, then exit
            stopping = True
            left = frame_ocr_queue.qsize()
            if left:
                events.emit("status", state="finalizing", detail=f"Reading text on {left} auto frame(s)")
            continue

        try:
            if not Path(image_path).exists():
                print(f"[OCR skip] file not found: {image_path}", file=sys.stderr)
                continue
            if reader is None:
                continue
            results = reader.readtext(image_path, detail=0, paragraph=True)
            text = "\n".join(results)
            txt_path = Path(image_path).with_suffix(".txt")
            txt_path.write_text(text, encoding="utf-8")
            time_str = datetime.now().strftime("%H:%M:%S")
            if frame_id is None:
                sys.stdout.write(f"\r\033[K[{time_str}] [OCR done: {txt_path.name}]\n")
                sys.stdout.flush()
            if frame_id is None:
                events.emit("ocr", path=image_path, txt=str(txt_path), chars=len(text))
            else:
                events.emit("frame_ocr", id=frame_id, path=image_path, chars=len(text))
        except Exception as e:
            print(f"[OCR error] {e}", file=sys.stderr)
            events.emit("ocr_error" if frame_id is None else "frame_ocr_error", path=image_path,
                        id=frame_id, message=str(e))
        finally:
            q.task_done()


def helper_reader(proc):
    """Read events from the helper subprocess (screenshots, selections)."""
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        if line.startswith("SCREENSHOT:"):
            path = line[len("SCREENSHOT:"):]
            marker = f"[screenshot: {path}]"
            pending_injections.put(marker)
            ocr_queue.put(path)
            events.emit("screenshot", path=path)
        elif line.startswith("FRAME:"):
            path = line[len("FRAME:"):]
            if live_frames is not None:
                live_frames.put(path)
            else:   # stray frame after AUTO OFF
                try:
                    os.unlink(path)
                except OSError:
                    pass
        elif line == "HOTKEY:SNAP":       # --hotkeys-only (video file): main.py takes the picture
            if hotkey_snap is not None:
                threading.Thread(target=hotkey_snap, daemon=True).start()
        elif line.startswith("SELECTION:"):
            b64text = line[len("SELECTION:"):]
            try:
                text = base64.b64decode(b64text).decode("utf-8")
                marker = f"[selection: {text}]"
                pending_injections.put(marker)
            except Exception as e:
                print(f"[selection error] {e}", file=sys.stderr)



def start_live_frames(frames_dir: Path, session_start: datetime, level1: bool = True, keep_all: bool = False):
    from autoframes import LiveFrames   # imported on demand: OpenCV is only needed for auto frames
    def on_scene(scene, kind):
        if kind in ("new", "revisit", "drift"):
            wall = datetime.fromisoformat(scene.wall_start).strftime("%H:%M:%S")
            label = {"new": f"frame #{scene.id}", "revisit": f"frame #{scene.id} = #{scene.same_as}",
                     "drift": f"frame #{scene.id} updated"}[kind]
            sys.stdout.write(f"\r\033[K[{wall}] [{label}]\n")
            sys.stdout.flush()
        if kind == "new" or kind == "drift":
            # The text on screen, like a screenshot's (for a drifted scene: its latest state)
            frame_ocr_queue.put((scene.last_path if kind == "drift" and scene.last_path else scene.path, scene.id))
        events.emit("frame_scene", kind=kind, id=scene.id, path=scene.path, last_path=scene.last_path,
                    same_as=scene.same_as, scroll_of=scene.scroll_of, t_start=round(scene.t_start, 2),
                    t_end=round(scene.t_end, 2), wall_start=scene.wall_start, wall_end=scene.wall_end,
                    changed=scene.changed, mask_frac=scene.mask_frac, cls=scene.cls,
                    cls_score=scene.cls_score)

    def on_stats(stats):
        events.emit_throttled("frame_stats", 1.0, "frame_stats", **stats)

    return LiveFrames(frames_dir, session_start, on_scene=on_scene, on_stats=on_stats, level1=level1,
                      drop_stale=not keep_all)


def shift_session_times(data, delta):
    """Shift all relative times in session data by *delta* seconds (in place)."""
    for seg in data["segments"]:
        seg.t_start += delta
        seg.t_end += delta
        for w in seg.words:
            w.t_start += delta
            w.t_end += delta
    for win in data["windows"]:
        win.t_start += delta
        win.t_end += delta
    data["injections"] = [(t + delta, m) for t, m in data["injections"]]
    data["fixed"] = [(t + delta, lbl, txt) for t, lbl, txt in data.get("fixed", [])]


def save_audio_recording(project_dir: Path, timestamp: str):
    """Save recorded audio chunks as MP3 (falls back to WAV if ffmpeg unavailable)."""
    import wave

    if not audio_chunks and not app_chunks:
        print("[audio] No audio recorded.")
        return

    audio = np.concatenate(audio_chunks) if audio_chunks else np.zeros(0, dtype=np.float32)
    if app_chunks:      # app+mic mode: mix the two channels, lined up on the session clock
        app = np.concatenate(app_chunks)
        shift = int((channel_start.get("app", 0.0) - channel_start.get("mic", 0.0)) * SAMPLE_RATE)
        app, audio = (np.pad(app, (shift, 0)), audio) if shift >= 0 else (app, np.pad(audio, (-shift, 0)))
        n = max(len(audio), len(app))
        audio = np.pad(audio, (0, n - len(audio))) + np.pad(app, (0, n - len(app)))
        audio = np.clip(audio, -1.0, 1.0)
    duration = len(audio) / SAMPLE_RATE
    print(f"[audio] Saving {duration:.1f}s of audio...")

    wav_path = project_dir / f"recording_{timestamp}.wav"
    mp3_path = project_dir / f"recording_{timestamp}.mp3"

    # Write WAV
    audio_int16 = (audio * 32767).astype(np.int16)
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_int16.tobytes())

    # Convert to MP3 via ffmpeg
    try:
        result = subprocess.run(
            [native_helpers.ffmpeg_exe() or "ffmpeg", "-y", "-loglevel", "error",
             "-i", str(wav_path), "-b:a", "64k", str(mp3_path)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            wav_path.unlink()
            print(f"[audio] Saved: {mp3_path}")
        else:
            print(f"[audio] ffmpeg error: {result.stderr.strip()}", file=sys.stderr)
            print(f"[audio] WAV kept: {wav_path}")
    except FileNotFoundError:
        print(f"[audio] ffmpeg not found, WAV kept: {wav_path}")


def pick_device(choice: str) -> tuple[str, str]:
    """(device, faster-whisper compute type) for --device auto|cpu|cuda."""
    if choice == "cpu":
        return "cpu", COMPUTE_TYPE
    native_helpers.add_cuda_dll_dirs()
    try:
        import ctranslate2
        has_cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        has_cuda = False
    if choice == "cuda" and not has_cuda:
        print("--device cuda: no CUDA device found, using the CPU", file=sys.stderr)
    return ("cuda", "float16") if has_cuda else ("cpu", COMPUTE_TYPE)


def main():
    global SILENCE_DURATION_MS, DEVICE, COMPUTE_TYPE
    native_helpers.utf8_stdio()

    parser = argparse.ArgumentParser(description="Real-time Russian speech-to-text")
    parser.add_argument("project", help="Project name (isolated directory for outputs)")
    parser.add_argument("minutes", type=float, help="Auto-stop after this many minutes")
    parser.add_argument("--save-audio", action="store_true", help="Save audio recording as MP3")
    parser.add_argument(
        "--lang", type=str, default=LANGUAGE,
        help="Language code (e.g. ru, en) or 'auto' for auto-detection (default: auto)",
    )
    parser.add_argument(
        "--pause", type=float, default=SILENCE_DURATION_MS / 1000,
        help=f"Silence duration to end a phrase in seconds (default: {SILENCE_DURATION_MS / 1000})",
    )
    parser.add_argument(
        "--speakers", type=str, default=None,
        help="Enable diarization: number of speakers (e.g. 2) or 'auto'",
    )
    parser.add_argument(
        "--cluster-threshold", type=float, default=0.7,
        help="Cosine distance threshold for auto speaker detection (default: 0.7)",
    )
    # Used by the web UI (server.py); console mode doesn't need them.
    parser.add_argument("--events", default=None,
                        help="Write structured JSON-lines events to this file")
    parser.add_argument("--control-stdin", action="store_true",
                        help="Accept commands on stdin: SNAP, STOP, PAUSE <sec>, MUTE, UNMUTE, CUT "
                             "(end the current phrase now), AUTO ON/OFF, MINUTES <min> (new auto-stop). "
                             "With a file source MUTE/UNMUTE pause/resume reading the file, and "
                             "SNAP [<sec>] takes the video frame at <sec> (default: the current position)")
    parser.add_argument("--wait-go", action="store_true",
                        help="With --control-stdin: load the models, report 'ready' and start capturing "
                             "only after GO on stdin (STOP or end of input quits)")
    parser.add_argument("--capture-config", default=None,
                        help="JSON capture config for the screenshot helper (area / window)")
    parser.add_argument("--continue-session", default=None, metavar="TIMESTAMP",
                        help="Append to an existing transcript_<TIMESTAMP>.txt instead of starting a new one")
    parser.add_argument("--vocab-file", default=None,
                        help='Notes file with \'- "heard" → "meant"\' lines; the correct terms are '
                             "passed to Whisper as hints (re-read on change)")
    parser.add_argument("--engine", choices=ENGINES, default="whisper",
                        help="Speech recognition engine: whisper (default, any language, hotwords), "
                             "gigaam (GigaAM v3, Russian only), parakeet (Parakeet TDT v3, ru/en/EU auto)")
    parser.add_argument("--asr-model", default=None,
                        help="Override the engine's model: a Whisper size, or an onnx-asr model name "
                             "(e.g. gigaam-v3-e2e-ctc)")
    parser.add_argument("--auto-frames", type=float, default=0, metavar="SEC",
                        help="Capture the screen every SEC seconds and keep the frames that show "
                             "something new (projects/<project>/frames/<session>/); 0 = off. "
                             "With --control-stdin it can also be toggled with AUTO ON/OFF")
    parser.add_argument("--no-frames-l1", action="store_true",
                        help="Auto frames without Level 1 (CLIP: drops faces, merges look-alike frames)")
    parser.add_argument("--vocab-correct", action="store_true",
                        help='Replace "heard" phrases from --vocab-file with the "meant" ones in the output '
                             "(useful with gigaam/parakeet, which have no hotwords)")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu",
                        help="Where Whisper and the speaker model run: cpu (default), cuda (NVIDIA GPU), "
                             "or auto (cuda when available)")
    parser.add_argument("--source", default="mic", metavar="SOURCE",
                        help="What to transcribe: mic (default); app:<bundle-id> - one app's audio, e.g. "
                             "app:com.microsoft.teams2 (macOS 14.2+); app+mic:<bundle-id> - the app and the "
                             "microphone, lines marked [Them] / [Me]; file:<path> - an audio file (mp3, m4a, "
                             "wav, ...) or a video file (mp4, mov, mkv, ...), timestamps are positions in "
                             "the file. See audio_helper --list. "
                             "On Windows the app is app:system - everything the speakers play")
    file_opts = parser.add_argument_group("file source (--source file:<path>)")
    file_opts.add_argument("--start", default=None, metavar="TIME",
                           help="Start at this position (seconds, MM:SS or HH:MM:SS; default: the beginning)")
    file_opts.add_argument("--end", default=None, metavar="TIME",
                           help="Stop at this position (default: the end of the file)")
    file_opts.add_argument("--realtime", action="store_true",
                           help="Read the file at playback speed (for watching along: Ctrl+Shift+S takes "
                                "the video frame on screen) instead of as fast as the recognizer goes")
    file_opts.add_argument("--play", action="store_true",
                           help="With --realtime: play the range in an ffplay window alongside")
    parser.add_argument("--no-echo-filter", action="store_true",
                        help="app+mic: keep mic phrases that only repeat the app's audio heard through the "
                             "speakers (by default they are dropped, so the other side isn't marked [Me])")
    args = parser.parse_args()
    source_kind, _, source_arg = args.source.partition(":")
    if source_kind not in ("mic", "app", "app+mic", "file") or (source_kind != "mic" and not source_arg):
        print(f"--source: expected mic, app:<bundle-id>, app+mic:<bundle-id> or file:<path>, got {args.source!r}",
              file=sys.stderr)
        sys.exit(1)
    audio_helper = native_helpers.audio_cmd()
    if source_kind in ("app", "app+mic") and not audio_helper:
        print(f"App audio needs audio_helper ({native_helpers.audio_build_hint()})", file=sys.stderr)
        sys.exit(1)
    DEVICE, COMPUTE_TYPE = pick_device(args.device)
    if source_kind == "file" and not Path(source_arg).is_file():
        print(f"Audio file not found: {source_arg}", file=sys.stderr)
        sys.exit(1)
    try:
        range_start = media_file.parse_time(args.start) or 0.0
        range_end = media_file.parse_time(args.end)
    except ValueError as e:
        print(f"--start/--end: {e}", file=sys.stderr)
        sys.exit(1)
    if source_kind != "file" and (args.start or args.end or args.realtime):
        print("--start, --end and --realtime need --source file:<path>", file=sys.stderr)
        sys.exit(1)
    media = None
    if source_kind == "file":
        try:
            media = media_file.probe(Path(source_arg))
        except Exception as e:
            print(f"Could not read {source_arg}: {e}", file=sys.stderr)
            sys.exit(1)
        if media.duration is not None:
            if range_end is not None and range_end > media.duration:
                range_end = None
            if range_start >= media.duration:
                print(f"--start {media_file.fmt_time(range_start)} is past the end of the file "
                      f"({media_file.fmt_time(media.duration)})", file=sys.stderr)
                sys.exit(1)
        if range_end is not None and range_end <= range_start:
            print("--end must be after --start", file=sys.stderr)
            sys.exit(1)
    if args.events:
        events.open(args.events)
        downloads.set_reporter(lambda ev: events.emit("download", **ev))
    global vocab_path
    if args.vocab_file:
        vocab_path = Path(args.vocab_file)
    SILENCE_DURATION_MS = int(args.pause * 1000)
    global vocab_correct
    vocab_correct = args.vocab_correct
    if args.engine == "gigaam" and args.lang.lower() not in ("auto", "ru"):
        print(f"--engine gigaam supports Russian only (got --lang {args.lang}); "
              "use --engine parakeet or whisper for other languages", file=sys.stderr)
        sys.exit(1)

    # Parse diarization settings
    enable_diarization = args.speakers is not None
    num_speakers = None
    if enable_diarization:
        if not _DIARIZATION_AVAILABLE:
            print("Diarization requires speaker_id.py and dependencies:", file=sys.stderr)
            print("  pip install speechbrain scikit-learn", file=sys.stderr)
            sys.exit(1)
        if args.speakers.lower() == "auto":
            num_speakers = None
        else:
            try:
                num_speakers = int(args.speakers)
            except ValueError:
                print(f"--speakers must be a number or 'auto', got: {args.speakers}",
                      file=sys.stderr)
                sys.exit(1)

    asr_engine, vad_model = load_models(args.engine, args.asr_model)
    if args.vocab_file and not asr_engine.supports_hotwords and not args.vocab_correct:
        print(f"[vocab] {args.engine} has no hotwords; vocab hints are ignored (add --vocab-correct "
              "to apply the notes' corrections to the output)")

    embedder = None
    if enable_diarization:
        print("Loading speaker embedding model (ECAPA-TDNN)...")
        events.emit("status", state="loading", detail="Loading speaker model")
        embedder = SpeakerEmbedder(device="cuda" if DEVICE == "cuda" and torch.cuda.is_available() else "cpu")
        print("Speaker model loaded.")
        if num_speakers:
            print(f"Diarization: {num_speakers} speaker(s)")
        else:
            print(f"Diarization: auto (threshold={args.cluster_threshold})")

    # Models are in memory: wait for the go-ahead so a long load doesn't start
    # recording while nobody is there. The session (and its clock) starts after GO.
    if args.wait_go and args.control_stdin:
        print("Models loaded. Waiting for GO on stdin...")
        events.emit("status", state="ready", detail="Models loaded")
        try:
            while True:
                line = sys.stdin.readline()
                cmd = line.strip().upper()
                if not line or cmd == "STOP":
                    print("Cancelled before recording started.")
                    sys.exit(0)
                if cmd == "GO":
                    break
                if cmd.startswith("PAUSE "):
                    try:
                        SILENCE_DURATION_MS = int(max(0.2, min(10.0, float(cmd.split()[1]))) * 1000)
                    except ValueError:
                        pass
                elif cmd.startswith("MINUTES "):
                    try:
                        args.minutes = max(1.0, float(cmd.split()[1]))
                    except ValueError:
                        pass
        except KeyboardInterrupt:
            print("Cancelled before recording started.")
            sys.exit(0)

    global screenshot_dir, save_audio_flag

    project_dir = Path("projects") / args.project
    output_dir = project_dir / "transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir = project_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    save_audio_flag = args.save_audio
    prefix_text = None          # existing transcript content when continuing a session
    if args.continue_session:
        timestamp = args.continue_session
        output_path = output_dir / f"transcript_{timestamp}.txt"
        if not output_path.exists():
            print(f"Transcript to continue not found: {output_path}", file=sys.stderr)
            sys.exit(1)
        prefix_text = output_path.read_text(encoding="utf-8")
        if prefix_text and not prefix_text.endswith("\n"):
            prefix_text += "\n"
            output_path.write_text(prefix_text, encoding="utf-8")
        output_file = open(output_path, "a", encoding="utf-8")
        print(f"Continuing session {timestamp} ({len(prefix_text.splitlines())} existing lines)")
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"transcript_{timestamp}.txt"
        output_file = open(output_path, "w", encoding="utf-8")

    # Shared session state
    global session_t0
    session_t0 = time.monotonic()
    session_data = {
        # A file's timestamps are positions in the file (00:00:00 = its start, also with --start).
        "start_time": datetime.combine(datetime.now().date(), datetime.min.time()) + timedelta(seconds=range_start)
        if source_kind == "file" else datetime.now(),
        "segments": [],     # list[AsrSegment]  - filled by transcription_worker
        "windows": [],      # list[EmbeddingWindow] - filled by embed_worker
        "injections": [],   # list[(t_relative, marker_text)]
        "fixed": [],        # list[(t_relative, label, text)] - lines with a known speaker ("Me")
    }
    events.emit("session", project=args.project, project_dir=str(project_dir.resolve()),
                transcript_path=str(output_path.resolve()), timestamp=timestamp,
                start_time=session_data["start_time"].isoformat(),
                diarization=enable_diarization, continued=prefix_text is not None, source=args.source,
                **({"range_start": range_start, "range_end": range_end, "realtime": args.realtime,
                    "video": media.video, "duration": media.duration} if media else {}))


    print(f"Project: {args.project} ({project_dir.resolve()})")
    if args.engine != "whisper":
        print(f"Engine: {asr_engine.label}")
    print(f"Language: {'ru' if args.engine == 'gigaam' else args.lang}")
    print(f"Silence threshold: {SILENCE_DURATION_MS}ms")

    # SIGINT handler
    def on_sigint(signum, frame):
        print("\nStopping...")
        events.emit("status", state="stopping")
        stop_event.set()

    signal.signal(signal.SIGINT, on_sigint)

    # Start transcription worker thread
    lang_mode = args.lang.lower()
    if args.engine == "gigaam":
        lang_mode = "ru"   # Russian-only model; no detection needed
        events.emit("lang", lang="ru")
    worker = threading.Thread(
        target=transcription_worker,
        args=(asr_engine, output_file, session_data, enable_diarization, lang_mode,
              LiveSpeakers(embedder, args.cluster_threshold, num_speakers) if enable_diarization else None),
        daemon=True,
    )
    worker.start()

    # Start embed worker if diarization enabled
    if enable_diarization:
        embed_thread = threading.Thread(
            target=embed_worker,
            args=(embedder, session_data),
            daemon=True,
        )
        embed_thread.start()

    # Start OCR worker thread
    ocr_thread = threading.Thread(target=ocr_worker, daemon=True)
    ocr_thread.start()

    # Audio sources. The main channel runs vad_loop on this thread; in app+mic mode the
    # app gets a second channel (own VAD, own thread) and lines are marked Me / Them.
    dual = source_kind == "app+mic"
    main_label = "Me" if dual else None
    stream = None
    app_proc = None
    app_vad_thread = None
    file_audio = None
    is_video = media is not None and media.video
    if source_kind == "file":
        span = "" if not (range_start or range_end) else \
            f" [{media_file.fmt_time(range_start)} - {media_file.fmt_time(range_end or media.duration or 0)}]"
        print(f"Decoding {source_arg}{span}...")
        events.emit("status", state="loading", detail="Decoding the audio")
        try:
            file_audio = decode_audio_file(Path(source_arg), project_dir, range_start, range_end)
        except Exception as e:
            print(f"Could not read the audio file: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"{'Video' if is_video else 'Audio'} file: {len(file_audio) / SAMPLE_RATE / 60:.1f} min{span}")
    if source_kind in ("mic", "app+mic"):
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=1600,  # 100ms blocks
            callback=audio_callback,
        )
        stream.start()
    if source_kind in ("app", "app+mic"):
        # stdin=DEVNULL: our stdin is the control pipe. On Windows a child inheriting it hangs on
        # startup (Python checks its stdin) while control_reader waits on it, until the next command.
        app_proc = subprocess.Popen([*audio_helper, "--capture", source_arg], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=helper_log_reader, args=(app_proc, "[app audio]"), daemon=True).start()
        threading.Thread(target=app_audio_reader,
                         args=(app_proc, app_queue if dual else audio_queue, app_chunks if dual else audio_chunks),
                         daemon=True).start()
        if dual:
            global app_loudness
            if not args.no_echo_filter:
                app_loudness = echo_gate.AppLoudness()
            app_vad_thread = threading.Thread(target=vad_loop, args=(load_vad(), app_queue, "Them", "app"),
                                              daemon=True)
            app_vad_thread.start()
    if file_audio is not None:
        threading.Thread(target=file_feeder, args=(file_audio, args.realtime, range_start), daemon=True).start()
    player_proc = None
    if args.play and args.realtime and file_audio is not None:
        ffplay = Path(native_helpers.ffmpeg_exe() or "ffmpeg").with_name("ffplay")
        if ffplay.exists():
            player_proc = subprocess.Popen([str(ffplay), "-loglevel", "error", "-autoexit",
                                            "-window_title", Path(source_arg).name,
                                            *media_file.range_args(range_start, range_end), source_arg],
                                           stdin=subprocess.DEVNULL)
        else:
            print("[play] ffplay not found next to ffmpeg; playing nothing", file=sys.stderr)

    # Start native screenshot helper. Not for audio files (nothing on screen belongs to them);
    # for a video watched in real time only its hotkeys: Ctrl+Shift+S takes the video's frame.
    global hotkey_snap
    file_path = Path(source_arg) if source_kind == "file" else None
    if is_video:
        hotkey_snap = lambda: video_snap(file_path, range_start)
    want_helper = source_kind != "file" or (is_video and args.realtime)
    screenshot_proc = None
    screenshot_helper = native_helpers.screenshot_cmd()
    if want_helper and not screenshot_helper:
        print(f"[screenshots] off: screenshot_helper is missing ({native_helpers.screenshot_build_hint()})",
              file=sys.stderr)
        events.emit("error", where="screenshots", message="Screenshots are off: the screenshot helper is missing")
    if want_helper and screenshot_helper:
        helper_cmd = [*screenshot_helper, str(screenshot_dir.resolve())]
        if args.capture_config:
            helper_cmd.append(str(Path(args.capture_config).resolve()))
        if source_kind == "file":
            helper_cmd.append("--hotkeys-only")
        screenshot_proc = subprocess.Popen(
            helper_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        helper_thread = threading.Thread(
            target=helper_reader, args=(screenshot_proc,), daemon=True
        )
        helper_thread.start()
        # Drain its stderr: a full pipe (4 KB on Windows) would block the helper.
        threading.Thread(target=helper_log_reader, args=(screenshot_proc, "[screenshots]"), daemon=True).start()

    frames_dir = project_dir / "frames" / timestamp
    frames_interval = args.auto_frames or 1.0
    frames_lock = threading.Lock()

    video_halt = [None, None]     # the running video_frames_worker's stop event and thread

    def set_auto_frames(on: bool, begin: float | None = None):
        global live_frames
        if is_video:
            with frames_lock:
                if video_halt[0] is not None:
                    video_halt[0].set()
                    video_halt[0] = None
                if on:
                    if live_frames is None:
                        live_frames = start_live_frames(frames_dir, session_data["start_time"],
                                                        level1=not args.no_frames_l1, keep_all=True)
                    video_halt[0] = threading.Event()
                    video_halt[1] = threading.Thread(target=video_frames_worker,
                                                     args=(file_path, range_start, range_end, frames_interval,
                                                           video_halt[0], begin),
                                                     daemon=True)
                    video_halt[1].start()
            print(f"\r\033[K[frames] auto {'on' if on else 'off'} (from the video)")
            events.emit("auto_frames", on=on, interval=frames_interval, dir=str(frames_dir.resolve()))
            return
        if screenshot_proc is None:
            return
        with frames_lock:
            if on and live_frames is None:
                live_frames = start_live_frames(frames_dir, session_data["start_time"],
                                                level1=not args.no_frames_l1)
            try:
                screenshot_proc.stdin.write(f"AUTO {frames_interval}\n" if on else "AUTO OFF\n")
                screenshot_proc.stdin.flush()
            except Exception as e:
                print(f"[frames] helper command failed: {e}", file=sys.stderr)
        print(f"\r\033[K[frames] auto {'on' if on else 'off'}")
        events.emit("auto_frames", on=on, interval=frames_interval, dir=str(frames_dir.resolve()))

    if args.auto_frames > 0:
        set_auto_frames(True, begin=0.0)

    # Auto-stop: a deadline (not a fixed Timer) so MINUTES can move it while recording
    listen_start = time.monotonic()
    auto_stop_at = listen_start + args.minutes * 60

    def set_auto_stop(minutes: float):
        nonlocal auto_stop_at
        args.minutes = max(1.0, minutes)
        auto_stop_at = listen_start + args.minutes * 60
        print(f"\r\033[K[auto-stop] {args.minutes:g} min")
        events.emit("minutes", minutes=args.minutes)

    def auto_stop_watch():
        while not stop_event.wait(1.0):
            if time.monotonic() >= auto_stop_at:
                print(f"\n[auto-stop] {args.minutes:g} min elapsed, finalizing...")
                events.emit("status", state="stopping", detail="auto-stop")
                stop_event.set()
                return

    if source_kind != "file":
        threading.Thread(target=auto_stop_watch, daemon=True).start()

    if args.control_stdin:
        def control_reader():
            for line in sys.stdin:
                cmd = line.strip().upper()
                if is_video and (cmd == "SNAP" or cmd.startswith("SNAP ")):
                    try:
                        at = float(cmd.split()[1]) if " " in cmd else None
                    except ValueError:
                        at = None
                    threading.Thread(target=video_snap, args=(file_path, range_start, at), daemon=True).start()
                elif cmd == "SNAP":
                    if screenshot_proc is None:
                        continue
                    try:
                        screenshot_proc.stdin.write("SNAP\n")
                        screenshot_proc.stdin.flush()
                    except Exception as e:
                        print(f"[control] snap failed: {e}", file=sys.stderr)
                elif cmd.startswith("PAUSE "):
                    global SILENCE_DURATION_MS
                    try:
                        SILENCE_DURATION_MS = int(max(0.2, min(10.0, float(cmd.split()[1]))) * 1000)
                        print(f"\r\033[K[pause] {SILENCE_DURATION_MS}ms")
                        events.emit("pause", seconds=SILENCE_DURATION_MS / 1000)
                    except ValueError:
                        pass
                elif cmd in ("MUTE", "UNMUTE"):
                    if cmd == "MUTE":
                        mic_muted.set()
                    else:
                        mic_muted.clear()
                    print(f"\r\033[K[{'file' if source_kind == 'file' else 'mic'}] "
                          f"{('paused' if source_kind == 'file' else 'muted') if cmd == 'MUTE' else 'live'}")
                    events.emit("muted", muted=cmd == "MUTE")
                elif cmd == "CUT":
                    global cut_requests
                    cut_requests += 1
                elif cmd in ("AUTO ON", "AUTO OFF"):
                    set_auto_frames(cmd == "AUTO ON")
                elif cmd.startswith("MINUTES "):
                    try:
                        set_auto_stop(float(cmd.split()[1]))
                    except ValueError:
                        pass
                elif cmd == "STOP":
                    events.emit("status", state="stopping")
                    stop_event.set()
        threading.Thread(target=control_reader, daemon=True).start()
    what = {"mic": "default microphone", "app": f"app audio of {source_arg}",
            "app+mic": f"app audio of {source_arg} [Them] + default microphone [Me]",
            "file": f"{'video' if is_video else 'audio'} file {Path(source_arg).name}"}[source_kind]
    if source_kind == "file":
        print(f"Transcribing {what}{' in real time' if args.realtime else ''} (Ctrl+C to stop early)...")
        if want_helper and screenshot_proc is not None:
            print("Ctrl+Shift+S: the video frame at the current position")
    else:
        print(f"Listening on {what} (auto-stop in {args.minutes} min, Ctrl+C to stop early)...")
        print(f"Ctrl+Shift+S: screenshot, Ctrl+Shift+W: selection")
    events.emit("status", state="listening", engine=args.engine, source=args.source)

    try:
        vad_loop(vad_model, audio_queue, main_label, {"file": None, "app": "app"}.get(source_kind, "mic"))
    finally:
        stop_event.set()        # the audio file ended, or a stop was requested
        if screenshot_proc is not None:
            screenshot_proc.terminate()
        if player_proc is not None:
            player_proc.terminate()
        if video_halt[0] is not None:
            # Let it take the frames up to where the audio got (it stops there by itself).
            video_halt[1].join(timeout=300)
            video_halt[0].set()
        if app_proc is not None:
            app_proc.terminate()
        if app_vad_thread is not None:
            app_vad_thread.join(timeout=5)    # its last phrase must be queued before the sentinel
        if live_frames is not None:
            live_frames.close()
            print(f"[frames] {len(live_frames.store.scenes)} scene(s): {live_frames.store.json_path}")
        for leftover in (screenshot_dir / ".frames").glob("frame_*.jpg"):
            leftover.unlink(missing_ok=True)
        if stream is not None:
            stream.stop()
            stream.close()
        events.emit("status", state="finalizing", detail="Transcribing remaining speech")
        # Signal workers to finish (sentinel after all real items)
        transcribe_queue.put(None)
        transcribe_queue.join()
        if enable_diarization:
            embed_queue.put(None)
            embed_queue.join()
        ocr_queue.put(None)
        ocr_queue.join()
        output_file.close()

        # Save audio recording
        if save_audio_flag:
            part = timestamp if prefix_text is None else \
                f"{timestamp}_part_{session_data['start_time']:%H%M%S}"
            save_audio_recording(project_dir, part)

        # Save session data for re-diarization
        if enable_diarization:
            import pickle
            session_path = project_dir / f"session_{timestamp}.pkl"
            raw_text = None
            prefix_for_output = ""
            diar_data = session_data
            if prefix_text is not None:
                # Continued session: the new raw lines are what was appended in this run.
                new_raw = output_path.read_text(encoding="utf-8")[len(prefix_text):]
                old_raw_path = output_path.with_suffix(".raw.txt")
                old_raw = old_raw_path.read_text(encoding="utf-8") if old_raw_path.exists() else prefix_text
                raw_text = old_raw + new_raw
                prev = None
                if session_path.exists():
                    try:
                        with open(session_path, "rb") as f:
                            prev = pickle.load(f)
                    except Exception as e:
                        print(f"[diarization] Could not load previous session data: {e}", file=sys.stderr)
                if prev:
                    # Re-cluster both parts together so speaker numbers stay consistent.
                    shift_session_times(session_data,
                                        (session_data["start_time"] - prev["start_time"]).total_seconds())
                    diar_data = {
                        "start_time": prev["start_time"],
                        "segments": prev["segments"] + session_data["segments"],
                        "windows": prev["windows"] + session_data["windows"],
                        "injections": prev["injections"] + session_data["injections"],
                        "fixed": prev.get("fixed", []) + session_data["fixed"],
                    }
                else:
                    # Earlier part has no speaker data: keep it as is, diarize only the new part.
                    prefix_for_output = prefix_text
            with open(session_path, "wb") as f:
                pickle.dump({
                    "start_time": diar_data["start_time"],
                    "segments": diar_data["segments"],
                    "windows": diar_data["windows"],
                    "injections": diar_data["injections"],
                    "fixed": diar_data["fixed"],
                    "transcript_path": str(output_path),
                }, f)
            print(f"Session data saved: {session_path}")

            finalize_diarization(
                diar_data, output_path, num_speakers, args.cluster_threshold,
                raw_text=raw_text, prefix_text=prefix_for_output,
            )

        print(f"Transcript saved to: {output_path}")
        if source_kind == "file":
            # Where to pick up after an interrupted run: --start <position>
            done = vad_pos >= len(file_audio) / SAMPLE_RATE - 0.05
            pos = range_start + vad_pos
            print(f"Processed {media_file.fmt_time(range_start)} - {media_file.fmt_time(pos)}"
                  f"{' (complete)' if done else f' (stopped early; resume with --start {pos:.1f})'}")
            events.emit("source_done", start=round(range_start, 2), position=round(pos, 2), complete=done)
        events.emit("finished", transcript_path=str(output_path.resolve()))
        events.close()


if __name__ == "__main__":
    main()
