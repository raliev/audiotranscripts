# voice-rec

Local, real-time speech-to-text with screenshot capture. Records voice commentary alongside screen captures, then uses an LLM to clean up the transcript using OCR'd screenshot text as reference context.

Built for narrating what's on screen -- record a coding session, walkthrough, or meeting, take screenshots at key moments, and get a polished document at the end.

## Features

- Real-time transcription, fully local (no cloud API), with a choice of engines: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (default), [GigaAM v3](https://github.com/salute-developers/GigaAM) or [Parakeet TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) via [onnx-asr](https://github.com/istupakov/onnx-asr) -- see [ASR engines](#asr-engines)
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
                                    | ASR engine (asr_engines)|
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
3. **Transcription worker** -- dequeues speech segments, runs the ASR engine (Whisper / GigaAM / Parakeet), writes timestamped lines to the transcript file
4. **Embedding worker** (optional) -- computes speaker embeddings for phrase-level audio chunks for post-session diarization
5. **OCR worker** -- processes screenshots through EasyOCR, saves recognized text alongside images
6. **Screenshot helper** -- a native macOS binary (Swift/Carbon) that registers system-wide hotkeys and captures the full screen, an area or a single window (configurable via a JSON file it re-reads on every capture). It also provides the area/window picker overlays used by the web UI (`--select-region`, `--pick-window`, `--list-windows`, `--capture`)

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

# Another recognition engine (see "ASR engines")
python main.py myproject --engine gigaam
python main.py myproject --engine parakeet --vocab-file notes.md --vocab-correct
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

### Web UI (studio)

A browser interface on top of the same recorder. The console mode above still works unchanged.

```bash
./webui.sh                      # or: python server.py  → http://127.0.0.1:8765
VOICE_REC_PYTHON=/path/to/python ./webui.sh --port 9000 --no-browser
```

- **Start / Stop**: runs `main.py` as a subprocess. Stop sends SIGINT, exactly like Ctrl+C.
- **Live transcript**: phrases appear as they're recognized, with a mic level meter and listening / transcribing state.
- **Screenshots panel**: each capture shows its pipeline status: Captured → Transcript → OCR → Beautified vN. `Snap` takes one without the hotkey.
- **Capture area**: full screen, a dragged **area**, or a specific **window**. A window is captured even when other windows cover it. Changes apply immediately, even mid-recording, so the UI itself can stay out of screenshots.
- **Beautify**: builds a *full* annotated transcript (every utterance, real speakers, NEW / CONFIRMS / UPDATES callouts, screenshots inline) through Claude Code (`claude -p`). Every press creates a new version:
  - *Update* (default) extends the previous version with the new material.
  - *Full rebuild* regenerates the whole document.
  - *Final* runs automatically when recording stops. It uses the diarized transcript and is also copied to `docs/`.
- **Ask**: chat about the transcript (live or saved) while recording keeps running. It follows up with only the new lines.

Per-project settings (gear icon) live in `projects/<name>/project.json`:
- **Knowledge folder**: Claude Code runs there, so its MCP servers (e.g. `claude-context`) are available for callouts.
- **Style reference**: the HTML whose look to copy. Defaults to `templates/annotated_transcript_style.html`.
- **Context notes**: who's who, client name, terms that are often misheard.

Beautify also works from the console:

```bash
python beautify.py myproject 20260928_110051 --mode full     # or: latest, --mode final
```

Files: versions in `projects/<p>/sessions/<sid>/vN.html` (+ `manifest.json`, transcript snapshots, `chat.json`, `events.jsonl`); UI state in `.webui/`.

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

## ASR engines

`--engine` picks the speech recognizer (`asr_engines.py`). All engines feed the same transcript format and word timestamps, so diarization, screenshots and the web UI work the same with each.

| Engine | Model | Languages | Notes |
|---|---|---|---|
| `whisper` (default) | faster-whisper `large-v3-turbo`, int8 | any, auto-detect | Supports vocab hints (`hotwords`); slowest on CPU; can hallucinate on silence |
| `gigaam` | GigaAM v3 e2e RNN-T (`gigaam-v3-e2e-rnnt`), int8 | Russian only | Best for Russian; punctuation, ё; `--lang en` is refused |
| `parakeet` | NVIDIA Parakeet TDT 0.6B v3, int8 | 25 European incl. ru/en, auto-detect | Good for mixed ru/en; weaker than GigaAM on Russian |

- `--asr-model` overrides the model: a Whisper size, or an onnx-asr name such as `gigaam-v3-e2e-ctc`.
- GigaAM and Parakeet have no `hotwords`. With `--vocab-correct`, `- "heard" → "meant"` lines from `--vocab-file` are applied to the output as replacements instead.
- Long phrases are split into ≤20 s (GigaAM) / ≤60 s (Parakeet) chunks at the quietest point, since the models degrade on long input.
- Models download into the Hugging Face cache on first use (GigaAM ~250 MB, Parakeet ~700 MB). To fetch them ahead of time:

  ```bash
  python -c "import onnx_asr; onnx_asr.load_model('gigaam-v3-e2e-rnnt', quantization='int8')"
  python -c "import onnx_asr; onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3', quantization='int8')"
  ```

Measured on an M3 Max, CPU, 140 s of Russian speech in 7 phrases (`bench_asr.py`):

| Engine | Compute | RTF | Peak RAM |
|---|---|---|---|
| whisper | 90.0 s | 0.64 | 2.3 GB |
| gigaam | 4.4 s | 0.031 | 0.7 GB |
| gigaam-ctc | 4.3 s | 0.030 | 0.6 GB |
| parakeet | 4.8 s | 0.034 | 1.6 GB |

To compare engines on your own recording (run it while the recorder is stopped):

```bash
python bench_asr.py projects/myproject/recording_20261007_083740.mp3 --start 60 --dur 300 --ref reference.txt
```

`--ref` is a hand-corrected transcript of the same range; with it the script reports WER/CER. Per-engine transcripts go to `bench_out/`.

## Configuration

Key constants in `main.py`:

| Parameter | Default | Description |
|---|---|---|
| `MODEL_SIZE` | `large-v3-turbo` | Whisper model variant (`--engine whisper`) |
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
| `asr_engines.py` | Speech recognition engines (Whisper, GigaAM, Parakeet) behind one interface |
| `bench_asr.py` | Compare engines on a recording: speed, memory, WER/CER |
| `screenshot_helper.swift` | Native macOS helper for global hotkeys and screen capture |
| `speaker_id.py` | Speaker embedding (ECAPA-TDNN) and agglomerative clustering |
| `rediarize.py` | Re-run diarization on saved sessions with different parameters |
| `process_transcript.py` | LLM-based transcript cleanup and HTML generation |
| `server.py` | Web UI server (FastAPI + WebSocket); drives `main.py`, Beautify and chat |
| `web/` | Web UI front end (static HTML/CSS/JS, no build step, Electron-ready) |
| `beautify.py` | Full annotated transcript via Claude Code (also a console tool) |
| `claude_cli.py` | Helpers for headless `claude -p` (stream-json parsing, progress labels) |
| `sessions.py` | Transcript parsing, session listing, settings, beautify manifests |
| `events.py` | Optional JSON-lines event stream from `main.py --events` |
| `templates/annotated_transcript_style.html` | Default style reference for Beautify |
| `requirements.txt` | Python dependencies |
