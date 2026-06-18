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
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
import easyocr
from faster_whisper import WhisperModel

# ── Config ───────────────────────────────────────────────────────────────────
MODEL_SIZE = "large-v3-turbo"
LANGUAGE = "ru"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
SAMPLE_RATE = 16000
VAD_THRESHOLD = 0.5
SILENCE_DURATION_MS = 1500
MIN_SPEECH_DURATION_MS = 250
OUTPUT_DIR = "transcripts"
SCREENSHOT_DIR = "screenshots"
SCREENSHOT_HELPER = Path(__file__).parent / "screenshot_helper"
BEAM_SIZE = 5

# ── Globals ──────────────────────────────────────────────────────────────────
audio_queue: queue.Queue[np.ndarray] = queue.Queue()
transcribe_queue: queue.Queue[np.ndarray] = queue.Queue()
stop_event = threading.Event()
screenshot_dir: Path | None = None
ocr_queue: queue.Queue[str] = queue.Queue()
pending_injections: queue.Queue[str] = queue.Queue()


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
    audio_queue.put(indata[:, 0].copy())


def flush_pending_injections(output_file):
    """Write pending injection markers (screenshots, selections) to transcript."""
    while True:
        try:
            marker = pending_injections.get_nowait()
        except queue.Empty:
            break
        time_str = datetime.now().strftime("%H:%M:%S")
        sys.stdout.write(f"\r\033[K[{time_str}] {marker}\n")
        sys.stdout.flush()
        output_file.write(marker + "\n")
        output_file.flush()


def transcription_worker(whisper_model, output_file):
    """Daemon thread: transcribe speech segments and stream results to console."""
    while True:
        try:
            segment_audio = transcribe_queue.get(timeout=0.5)
        except queue.Empty:
            flush_pending_injections(output_file)
            if stop_event.is_set() and transcribe_queue.empty():
                break
            continue

        duration = len(segment_audio) / SAMPLE_RATE
        sys.stdout.write(f"\r\033[K[распознаю {duration:.1f}с...] ")
        sys.stdout.flush()

        try:
            segments, info = whisper_model.transcribe(
                segment_audio,
                language=LANGUAGE,
                beam_size=BEAM_SIZE,
                vad_filter=False,
            )
            timestamp = datetime.now().strftime("%H:%M:%S")
            first = True
            has_text = False
            for seg in segments:
                text = seg.text.strip()
                if text:
                    if first:
                        sys.stdout.write(f"\r\033[K[{timestamp}] ")
                        first = False
                    sys.stdout.write(text + " ")
                    sys.stdout.flush()
                    output_file.write(text + " ")
                    has_text = True

            if has_text:
                sys.stdout.write("\n")
                sys.stdout.flush()
                output_file.write("\n")
                output_file.flush()
            else:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
        except Exception as e:
            print(f"\r\033[K[transcription error] {e}", file=sys.stderr)
        finally:
            transcribe_queue.task_done()
        flush_pending_injections(output_file)


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

            if speech_prob >= VAD_THRESHOLD:
                if not is_speaking:
                    is_speaking = True
                    silent_count = 0
                    speech_count = 0
                speech_count += 1
                silent_count = 0
                speech_buffer = np.concatenate([speech_buffer, window])
                # Show recording status with duration
                duration = len(speech_buffer) / SAMPLE_RATE
                sys.stdout.write(f"\r\033[K● речь [{duration:.1f}с]")
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
                            transcribe_queue.put(speech_buffer.copy())
                        speech_buffer = np.array([], dtype=np.float32)
                        is_speaking = False
                        silent_count = 0
                        speech_count = 0

    # Flush remaining speech buffer on shutdown
    if len(speech_buffer) > 0 and speech_count >= min_speech_chunks:
        transcribe_queue.put(speech_buffer.copy())


def ocr_worker():
    """Daemon thread: OCR screenshots and save text to .txt files."""
    reader = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
    while True:
        try:
            image_path = ocr_queue.get(timeout=0.5)
        except queue.Empty:
            if stop_event.is_set() and ocr_queue.empty():
                break
            continue
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



def main():
    global SILENCE_DURATION_MS

    parser = argparse.ArgumentParser(description="Real-time Russian speech-to-text")
    parser.add_argument(
        "--pause", type=float, default=SILENCE_DURATION_MS / 1000,
        help=f"Пауза в секундах для завершения фразы (default: {SILENCE_DURATION_MS / 1000})",
    )
    args = parser.parse_args()
    SILENCE_DURATION_MS = int(args.pause * 1000)

    global screenshot_dir

    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)
    screenshot_dir = Path(SCREENSHOT_DIR)
    screenshot_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"transcript_{timestamp}.txt"
    output_file = open(output_path, "w", encoding="utf-8")

    whisper_model, vad_model = load_models()
    print(f"Пауза для распознавания: {SILENCE_DURATION_MS}мс")

    # SIGINT handler
    def on_sigint(signum, frame):
        print("\nStopping...")
        stop_event.set()

    signal.signal(signal.SIGINT, on_sigint)

    # Start transcription worker thread
    worker = threading.Thread(
        target=transcription_worker,
        args=(whisper_model, output_file),
        daemon=True,
    )
    worker.start()

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
        # Wait for pending transcriptions and OCR to finish
        transcribe_queue.join()
        ocr_queue.join()
        output_file.close()
        print(f"Transcript saved to: {output_path}")


if __name__ == "__main__":
    main()
