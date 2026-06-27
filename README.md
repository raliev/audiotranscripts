# voice-rec

Local, real-time speech-to-text with screenshot capture. Records voice commentary alongside screen captures, then uses an LLM to clean up the transcript using OCR'd screenshot text as reference context.

Built for narrating what's on screen -- record a coding session, walkthrough, or meeting, take screenshots at key moments, and get a polished document at the end.

## Features

- Real-time transcription using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (runs locally, no cloud API)
- Voice Activity Detection via [Silero VAD](https://github.com/snakers4/silero-vad) -- only transcribes when you speak
- Automatic language detection (Russian/English) with periodic re-evaluation
- Global hotkeys for screenshots (`Ctrl+Shift+S`) and text selection capture (`Ctrl+Shift+W`)
- OCR on screenshots via [EasyOCR](https://github.com/JaidedAI/EasyOCR) (Russian + English)
- Optional speaker diarization using [SpeechBrain](https://github.com/speechbrain/speechbrain) ECAPA-TDNN embeddings
- Post-processing: send raw transcript + OCR text to an LLM for cleanup and HTML generation
- Audio recording export (MP3 via ffmpeg)

## Architecture

```
                  +-----------+
Microphone -----> | sounddevice|
                  +-----+-----+
                        |
                  float32 audio chunks
                        |
                  +-----v-----+       +------------------+
                  | Silero VAD | ----> | transcribe_queue |
                  +-----------+       +--------+---------+
                                               |
                                    +----------v-----------+
                                    | Whisper (faster-whisper)|
                                    +----------+-----------+
                                               |
                         +---------------------+---------------------+
                         |                                           |
                  +------v------+                             +------v------+
                  | Transcript  |                             | embed_queue |
                  |  (.txt file)|                             +------+------+
                  +-------------+                                    |
                                                           +---------v--------+
                                                           | SpeechBrain      |
                                                           | ECAPA-TDNN       |
                                                           | (speaker embeds) |
                                                           +------------------+

  +-------------------+       +----------+       +-----------+
  | screenshot_helper | ----> | OCR      | ----> | .txt next |
  | (Swift, global    |       | (EasyOCR)|       | to .png   |
  |  hotkeys)         |       +----------+       +-----------+
  +-------------------+
```

The system runs several threads concurrently:

1. **Audio capture** -- `sounddevice` streams microphone input at 16kHz mono
2. **VAD loop** (main thread) -- Silero VAD processes 32ms windows, detects speech start/end based on configurable silence threshold
3. **Transcription worker** -- dequeues speech segments, runs Whisper, writes timestamped lines to the transcript file
4. **Embedding worker** (optional) -- computes speaker embeddings for phrase-level audio chunks for post-session diarization
5. **OCR worker** -- processes screenshots through EasyOCR, saves recognized text alongside images
6. **Screenshot helper** -- a native macOS binary (Swift/Carbon) that registers system-wide hotkeys and captures the screen via `CGDisplayCreateImage`

The screenshot helper communicates with Python through stdout: it prints `SCREENSHOT:<path>` or `SELECTION:<base64>` lines that the main process reads and injects as markers into the transcript.

## Requirements

- macOS (screenshot helper uses Carbon/Cocoa APIs)
- Python 3.10+
- ffmpeg (optional, for MP3 audio export)
- Screen Recording permission (for screenshots)
- Accessibility permission (for text selection capture)

## Setup

```bash
# Install Python dependencies
pip install -r requirements.txt

# Compile the screenshot helper
swiftc -O -o screenshot_helper screenshot_helper.swift \
    -framework Cocoa -framework Carbon -framework CoreGraphics
```

## Usage

### Recording

```bash
# Basic recording (auto language detection)
python main.py myproject

# Specify language
python main.py myproject --lang ru

# With speaker diarization (2 speakers)
python main.py myproject --speakers 2

# Auto-detect number of speakers
python main.py myproject --speakers auto

# Save audio recording
python main.py myproject --save-audio

# Adjust silence threshold (seconds)
python main.py myproject --pause 2.0
```

During recording:
- `Ctrl+Shift+S` -- take a screenshot
- `Ctrl+Shift+W` -- capture selected text
- `Ctrl+C` -- stop recording

Output is organized per project:

```
projects/
  myproject/
    transcripts/
      transcript_20260618_143022.txt
    screenshots/
      screenshot_20260618_143045_123.png
      screenshot_20260618_143045_123.txt   # OCR result
    session_20260618_143022.pkl            # diarization data
```

### Re-diarization

If speaker labels need tuning, re-run diarization on saved session data without re-recording:

```bash
# Inspect embeddings and distance matrix
python rediarize.py myproject --debug

# Sweep threshold values to find optimal setting
python rediarize.py myproject --sweep

# Re-diarize with specific parameters
python rediarize.py myproject --speakers 2
python rediarize.py myproject --cluster-threshold 0.4
```

### Post-processing

Clean up transcripts using an LLM (Gemini, OpenAI, or OpenRouter). The LLM corrects speech recognition errors using OCR'd screenshot text as reference, then generates HTML output.

```bash
# Set API key
export GEMINI_API_KEY=...   # or OPENAI_API_KEY, OPENROUTER_API_KEY

# Process a day's transcripts
python process_transcript.py myproject 2026-06-18

# Date range
python process_transcript.py myproject 2026-06-17 --to 2026-06-18

# Time range within a day
python process_transcript.py myproject 2026-06-18 --time-from 14:00 --time-to 15:30

# Choose model/provider
python process_transcript.py myproject 2026-06-18 --model gpt-4.1-mini
python process_transcript.py myproject 2026-06-18 --provider openrouter
```

Output goes to `projects/myproject/docs/` as both `.txt` and `.html`.

## Configuration

Key constants in `main.py`:

| Parameter | Default | Description |
|---|---|---|
| `MODEL_SIZE` | `large-v3-turbo` | Whisper model variant |
| `DEVICE` | `cpu` | Inference device (`cpu` or `cuda`) |
| `COMPUTE_TYPE` | `int8` | Quantization for faster-whisper |
| `VAD_THRESHOLD` | `0.5` | Speech probability threshold |
| `SILENCE_DURATION_MS` | `1500` | Silence before ending a phrase |
| `MIN_SPEECH_DURATION_MS` | `250` | Minimum speech to transcribe |
| `BEAM_SIZE` | `5` | Whisper beam search width |

## File overview

| File | Purpose |
|---|---|
| `main.py` | Main app: audio capture, VAD, transcription, screenshot integration |
| `screenshot_helper.swift` | Native macOS helper for global hotkeys and screen capture |
| `speaker_id.py` | Speaker embedding (ECAPA-TDNN) and agglomerative clustering |
| `rediarize.py` | Re-run diarization on saved sessions with different parameters |
| `process_transcript.py` | LLM-based transcript cleanup and HTML generation |
| `requirements.txt` | Python dependencies |
