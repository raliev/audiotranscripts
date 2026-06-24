#!/usr/bin/env python3
"""Real-time Russian speech-to-text using local Whisper model."""

import argparse
import base64
import os
import signal
import subprocess
import sys
import threading
import queue
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
import easyocr
from faster_whisper import WhisperModel

try:
    from speaker_id import (
        SpeakerEmbedder, WordToken, AsrSegment, EmbeddingWindow,
        cluster_and_label, diarize_words,
    )
    _DIARIZATION_AVAILABLE = True
except ImportError:
    _DIARIZATION_AVAILABLE = False

# ── Config ───────────────────────────────────────────────────────────────────
MODEL_SIZE = "large-v3-turbo"
LANGUAGE = "auto"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
SAMPLE_RATE = 16000
VAD_THRESHOLD = 0.5
SILENCE_DURATION_MS = 1500
MIN_SPEECH_DURATION_MS = 250
SCREENSHOT_HELPER = Path(__file__).parent / "screenshot_helper"
BEAM_SIZE = 5

# ── Diarization config ──────────────────────────────────────────────────────
PHRASE_GAP_SEC = 0.4       # gap between words to force a phrase split
EMBED_CHUNK_SEC = 3.0      # max phrase duration before forced split (at word boundary)
MIN_PHRASE_AUDIO_SEC = 1.0 # min audio duration for reliable speaker embedding

# ── Globals ──────────────────────────────────────────────────────────────────
audio_queue: queue.Queue[np.ndarray] = queue.Queue()
transcribe_queue: queue.Queue = queue.Queue()      # items: (t_start, audio)
embed_queue: queue.Queue = queue.Queue()            # items: (t_start, t_end, audio)
stop_event = threading.Event()
screenshot_dir: Path | None = None
ocr_queue: queue.Queue[str] = queue.Queue()
pending_injections: queue.Queue[str] = queue.Queue()
audio_chunks: list[np.ndarray] = []
save_audio_flag = False


def load_models():
    """Load Whisper model and Silero VAD."""
    print(f"Loading Whisper model '{MODEL_SIZE}' (device={DEVICE}, compute={COMPUTE_TYPE})...")
    whisper_model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    print("Whisper model loaded.")

    print("Loading Silero VAD...")
    vad_model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        trust_repo=True,
    )
    print("Silero VAD loaded.")
    return whisper_model, vad_model


def audio_callback(indata, frames, time_info, status):
    """sounddevice callback — push mono float32 frames into audio_queue."""
    if status:
        print(f"[audio] {status}", file=sys.stderr)
    data = indata[:, 0].copy()
    audio_queue.put(data)
    if save_audio_flag:
        audio_chunks.append(data)


def flush_pending_injections(output_file, session_data):
    """Write pending injection markers (screenshots, selections) to transcript."""
    while True:
        try:
            marker = pending_injections.get_nowait()
        except queue.Empty:
            break
        t_rel = (datetime.now() - session_data["start_time"]).total_seconds()
        wall_time = datetime.now().strftime("%H:%M:%S")
        sys.stdout.write(f"\r\033[K[{wall_time}] {marker}\n")
        sys.stdout.flush()
        output_file.write(f"[{wall_time}] {marker}\n")
        output_file.flush()
        session_data["injections"].append((t_rel, marker))


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


# ── Language detection config ──────────────────────────────────────────────
LANG_FIRST_CHECK = 30      # words before first detection
LANG_RECHECK_INTERVAL = 200 # words between periodic re-checks
LANG_WINDOW = 300           # sliding window size for re-checks


def transcription_worker(whisper_model, output_file, session_data, enable_diarization, lang_mode):
    """Daemon thread: transcribe speech segments and stream results to console."""
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

        seg_t_start, segment_audio = item
        duration = len(segment_audio) / SAMPLE_RATE
        sys.stdout.write(f"\r\033[K[transcribing {duration:.1f}s...] ")
        sys.stdout.flush()

        try:
            segments, info = whisper_model.transcribe(
                segment_audio,
                language=current_lang,
                beam_size=BEAM_SIZE,
                vad_filter=False,
                word_timestamps=enable_diarization,
            )

            # Wall-clock timestamp derived from sample-accurate time
            wall_time = session_data["start_time"] + timedelta(seconds=seg_t_start)
            timestamp = wall_time.strftime("%H:%M:%S")

            first = True
            has_text = False
            asr_words = []
            seg_texts = []

            for seg in segments:
                text = seg.text.strip()
                if text:
                    if first:
                        sys.stdout.write(f"\r\033[K[{timestamp}] ")
                        output_file.write(f"[{timestamp}] ")
                        first = False
                    sys.stdout.write(text + " ")
                    sys.stdout.flush()
                    output_file.write(text + " ")
                    has_text = True
                    seg_texts.append(text)

                # Collect word-level data for diarization
                if enable_diarization and seg.words:
                    for w in seg.words:
                        word_text = w.word.strip()
                        if word_text:
                            asr_words.append(WordToken(
                                text=word_text,
                                t_start=seg_t_start + w.start,
                                t_end=seg_t_start + w.end,
                            ))

            if has_text:
                sys.stdout.write("\n")
                sys.stdout.flush()
                output_file.write("\n")
                output_file.flush()

                if enable_diarization and asr_words:
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
                    next_lang_check = len(lang_words) + LANG_RECHECK_INTERVAL

        except Exception as e:
            print(f"\r\033[K[transcription error] {e}", file=sys.stderr)
        finally:
            transcribe_queue.task_done()
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
        except Exception as e:
            print(f"[embed error] {e}", file=sys.stderr)
        finally:
            embed_queue.task_done()
    if count:
        sys.stdout.write(f"\r\033[K[embedding] done ({count} phrases)\n")
        sys.stdout.flush()


def vad_loop(vad_model):
    """Main-thread VAD loop: detect speech segments and enqueue for transcription."""
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

    while not stop_event.is_set():
        # Drain audio_queue into buffer
        try:
            chunk = audio_queue.get(timeout=0.1)
            buffer = np.concatenate([buffer, chunk])
        except queue.Empty:
            continue

        # Process all complete VAD windows
        while len(buffer) >= vad_chunk_samples:
            window = buffer[:vad_chunk_samples]
            buffer = buffer[vad_chunk_samples:]

            tensor = torch.from_numpy(window)
            speech_prob = vad_model(tensor, SAMPLE_RATE).item()
            is_speech = speech_prob >= VAD_THRESHOLD

            samples_processed += vad_chunk_samples

            if is_speech:
                if not is_speaking:
                    is_speaking = True
                    silent_count = 0
                    speech_count = 0
                    speech_start_sample = samples_processed - vad_chunk_samples
                speech_count += 1
                silent_count = 0
                speech_buffer = np.concatenate([speech_buffer, window])
                # Show recording status with duration
                duration = len(speech_buffer) / SAMPLE_RATE
                sys.stdout.write(f"\r\033[K● speech [{duration:.1f}s]")
                sys.stdout.flush()
            else:
                if is_speaking:
                    silent_count += 1
                    speech_buffer = np.concatenate([speech_buffer, window])

                    if silent_count >= silence_chunks:
                        sys.stdout.write("\r\033[K")
                        sys.stdout.flush()
                        # End of speech segment
                        if speech_count >= min_speech_chunks:
                            t_start = speech_start_sample / SAMPLE_RATE
                            transcribe_queue.put((t_start, speech_buffer.copy()))
                        speech_buffer = np.array([], dtype=np.float32)
                        is_speaking = False
                        silent_count = 0
                        speech_count = 0

    # Flush remaining speech buffer on shutdown
    if len(speech_buffer) > 0 and speech_count >= min_speech_chunks:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        t_start = speech_start_sample / SAMPLE_RATE
        transcribe_queue.put((t_start, speech_buffer.copy()))


def finalize_diarization(session_data, output_path, num_speakers, cluster_threshold):
    """Post-process: cluster embeddings, label words by speaker, rewrite transcript."""
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
    labels = cluster_and_label(windows, num_speakers, cluster_threshold)

    n_speakers = len(set(labels))
    print(f"[diarization] Found {n_speakers} speaker(s).")

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
            lines.append(f"[{time_str}] [Speaker {speaker}] {content}")
        else:
            lines.append(f"[{time_str}] {content}")

    # Keep provisional as .raw.txt, write diarized to original path
    raw_path = output_path.with_suffix(".raw.txt")
    output_path.rename(raw_path)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[diarization] Raw transcript kept: {raw_path}")
    print(f"[diarization] Diarized transcript: {output_path}")


def ocr_worker():
    """Daemon thread: OCR screenshots and save text to .txt files."""
    reader = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
    while True:
        try:
            image_path = ocr_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if image_path is None:  # sentinel → exit
            ocr_queue.task_done()
            break

        try:
            if not Path(image_path).exists():
                print(f"[OCR skip] file not found: {image_path}", file=sys.stderr)
                continue
            results = reader.readtext(image_path, detail=0, paragraph=True)
            text = "\n".join(results)
            txt_path = Path(image_path).with_suffix(".txt")
            txt_path.write_text(text, encoding="utf-8")
            time_str = datetime.now().strftime("%H:%M:%S")
            sys.stdout.write(f"\r\033[K[{time_str}] [OCR done: {txt_path.name}]\n")
            sys.stdout.flush()
        except Exception as e:
            print(f"[OCR error] {e}", file=sys.stderr)
        finally:
            ocr_queue.task_done()


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
        elif line.startswith("SELECTION:"):
            b64text = line[len("SELECTION:"):]
            try:
                text = base64.b64decode(b64text).decode("utf-8")
                marker = f"[selection: {text}]"
                pending_injections.put(marker)
            except Exception as e:
                print(f"[selection error] {e}", file=sys.stderr)



def save_audio_recording(project_dir: Path, timestamp: str):
    """Save recorded audio chunks as MP3 (falls back to WAV if ffmpeg unavailable)."""
    import wave

    if not audio_chunks:
        print("[audio] No audio recorded.")
        return

    audio = np.concatenate(audio_chunks)
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
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", str(wav_path), "-b:a", "64k", str(mp3_path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            wav_path.unlink()
            print(f"[audio] Saved: {mp3_path}")
        else:
            print(f"[audio] ffmpeg error: {result.stderr.strip()}", file=sys.stderr)
            print(f"[audio] WAV kept: {wav_path}")
    except FileNotFoundError:
        print(f"[audio] ffmpeg not found, WAV kept: {wav_path}")


def main():
    global SILENCE_DURATION_MS

    parser = argparse.ArgumentParser(description="Real-time Russian speech-to-text")
    parser.add_argument("project", help="Project name (isolated directory for outputs)")
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
        "--cluster-threshold", type=float, default=0.6,
        help="Cosine distance threshold for auto speaker detection (default: 0.6)",
    )
    args = parser.parse_args()
    SILENCE_DURATION_MS = int(args.pause * 1000)

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

    global screenshot_dir, save_audio_flag

    project_dir = Path("projects") / args.project
    output_dir = project_dir / "transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir = project_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    save_audio_flag = args.save_audio
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"transcript_{timestamp}.txt"
    output_file = open(output_path, "w", encoding="utf-8")

    # Shared session state
    session_data = {
        "start_time": datetime.now(),
        "segments": [],     # list[AsrSegment]  — filled by transcription_worker
        "windows": [],      # list[EmbeddingWindow] — filled by embed_worker
        "injections": [],   # list[(t_relative, marker_text)]
    }

    whisper_model, vad_model = load_models()

    embedder = None
    if enable_diarization:
        print("Loading speaker embedding model (ECAPA-TDNN)...")
        embedder = SpeakerEmbedder(device=DEVICE)
        print("Speaker model loaded.")
        if num_speakers:
            print(f"Diarization: {num_speakers} speaker(s)")
        else:
            print(f"Diarization: auto (threshold={args.cluster_threshold})")

    print(f"Project: {args.project} ({project_dir.resolve()})")
    print(f"Language: {args.lang}")
    print(f"Silence threshold: {SILENCE_DURATION_MS}ms")

    # SIGINT handler
    def on_sigint(signum, frame):
        print("\nStopping...")
        stop_event.set()

    signal.signal(signal.SIGINT, on_sigint)

    # Start transcription worker thread
    lang_mode = args.lang.lower()
    worker = threading.Thread(
        target=transcription_worker,
        args=(whisper_model, output_file, session_data, enable_diarization, lang_mode),
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

    # Start audio stream
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=1600,  # 100ms blocks
        callback=audio_callback,
    )
    stream.start()

    # Start native screenshot helper
    screenshot_proc = subprocess.Popen(
        [str(SCREENSHOT_HELPER), str(screenshot_dir.resolve())],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    helper_thread = threading.Thread(
        target=helper_reader, args=(screenshot_proc,), daemon=True
    )
    helper_thread.start()
    print(f"Listening on default microphone (Ctrl+C to stop)...")
    print(f"Ctrl+Shift+S: screenshot, Ctrl+Shift+W: selection")

    try:
        vad_loop(vad_model)
    finally:
        screenshot_proc.terminate()
        stream.stop()
        stream.close()
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
            save_audio_recording(project_dir, timestamp)

        # Save session data for re-diarization
        if enable_diarization:
            import pickle
            session_path = project_dir / f"session_{timestamp}.pkl"
            with open(session_path, "wb") as f:
                pickle.dump({
                    "start_time": session_data["start_time"],
                    "segments": session_data["segments"],
                    "windows": session_data["windows"],
                    "injections": session_data["injections"],
                    "transcript_path": str(output_path),
                }, f)
            print(f"Session data saved: {session_path}")

            finalize_diarization(
                session_data, output_path, num_speakers, args.cluster_threshold,
            )

        print(f"Transcript saved to: {output_path}")


if __name__ == "__main__":
    main()
