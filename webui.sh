#!/bin/bash
# Launch the voice-rec web UI (http://127.0.0.1:8765).
# Uses $VOICE_REC_PYTHON if set — it must have faster-whisper, torch, easyocr, fastapi and uvicorn.
cd "$(dirname "$0")"
exec "${VOICE_REC_PYTHON:-python3}" server.py "$@"
