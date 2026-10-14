"""Pluggable speech-recognition engines for main.py.

Every engine returns segments shaped like faster-whisper's (seg.text, seg.start,
seg.end, seg.words[].word/.start/.end; times relative to the audio passed in), so
transcription_worker treats them all the same way.

  whisper   faster-whisper large-v3-turbo (default; supports hotwords, any language)
  gigaam    GigaAM v3 e2e RNN-T via onnx-asr (Russian only, punctuation, no torch)
  parakeet  NVIDIA Parakeet TDT 0.6B v3 via onnx-asr (25 European languages incl.
            ru/en, auto-detected; punctuation)
"""

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

SAMPLE_RATE = 16000


@dataclass
class Word:
    word: str
    start: float
    end: float


@dataclass
class Segment:
    text: str
    start: float
    end: float
    words: list = field(default_factory=list)


class AsrEngine:
    name = ""
    label = ""                  # human-readable, for status lines
    supports_hotwords = False
    languages: set | None = None  # None = any language

    def transcribe(self, audio: np.ndarray, *, language: str | None,
                   word_timestamps: bool, hotwords: str | None) -> Iterable:
        raise NotImplementedError


# ── Whisper ────────────────────────────────────────────────────────────────────
class WhisperEngine(AsrEngine):
    name = "whisper"
    supports_hotwords = True

    def __init__(self, model_size: str, device: str, compute_type: str, beam_size: int):
        from faster_whisper import WhisperModel
        self.label = f"Whisper {model_size}"
        self.beam_size = beam_size
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, audio, *, language, word_timestamps, hotwords):
        # Returns faster-whisper's lazy generator unchanged, so segments still stream.
        segments, _info = self.model.transcribe(
            audio,
            language=language,
            beam_size=self.beam_size,
            vad_filter=False,
            word_timestamps=word_timestamps,
            hotwords=hotwords,
        )
        return segments


# ── onnx-asr (GigaAM, Parakeet) ──────────────────────────────────────────────────
ONNX_PRESETS = {
    # engine: (default onnx-asr model, label, languages, max chunk seconds)
    # GigaAM degrades past ~25 s of input and returns nothing at 120 s; Parakeet was
    # checked to be fine at 120 s but is chunked too to bound latency and memory.
    "gigaam": ("gigaam-v3-e2e-rnnt", "GigaAM v3", {"ru"}, 20.0),
    "parakeet": ("nemo-parakeet-tdt-0.6b-v3", "Parakeet TDT v3", None, 60.0),
}
MIN_AUDIO_SEC = 0.1     # shorter input crashes the GigaAM preprocessor
CUT_SEARCH_SEC = 4.0    # look for the quietest point in the last N seconds of a chunk
CUT_WINDOW = 512        # 32 ms RMS windows when searching for a cut
MAX_WORD_SEC = 0.8      # cap a word's end when the next word starts much later


class OnnxAsrEngine(AsrEngine):
    def __init__(self, engine: str, model_name: str | None = None, quantization: str | None = "int8"):
        import onnx_asr
        default_model, label, languages, max_chunk = ONNX_PRESETS[engine]
        self.name = engine
        self.model_name = model_name or default_model
        self.label = f"{label} ({self.model_name}, {quantization or 'fp32'})"
        self.languages = languages
        self.max_chunk_sec = max_chunk
        self.model = onnx_asr.load_model(
            self.model_name, quantization=quantization, providers=["CPUExecutionProvider"],
        ).with_timestamps()

    def transcribe(self, audio, *, language, word_timestamps, hotwords):
        # language / hotwords: not supported by these models (Parakeet auto-detects).
        audio = np.asarray(audio, dtype=np.float32)
        for start, end in split_chunks(audio, self.max_chunk_sec):
            chunk = audio[start:end]
            offset = start / SAMPLE_RATE
            chunk_end = end / SAMPLE_RATE
            if len(chunk) < MIN_AUDIO_SEC * SAMPLE_RATE:
                continue
            res = self.model.recognize(chunk, sample_rate=SAMPLE_RATE)
            text = (res.text or "").strip()
            if start > 0:
                # GigaAM marks speech cut off at the chunk start with "..."; the cut is ours.
                text = text.lstrip(".… ")
            if not text:
                continue
            words = tokens_to_words(res.tokens or [], res.timestamps or [], offset, chunk_end)
            yield Segment(text=text, start=offset, end=chunk_end, words=words)


def split_chunks(audio: np.ndarray, max_sec: float) -> list[tuple[int, int]]:
    """Split audio into [start, end) sample ranges of at most max_sec, cutting at the
    quietest 32 ms window in the last CUT_SEARCH_SEC of each chunk."""
    n = len(audio)
    max_len = int(max_sec * SAMPLE_RATE)
    if n <= max_len:
        return [(0, n)]
    ranges = []
    start = 0
    while n - start > max_len:
        hard_end = start + max_len
        search_from = max(start + 1, hard_end - int(CUT_SEARCH_SEC * SAMPLE_RATE))
        region = audio[search_from:hard_end]
        n_win = len(region) // CUT_WINDOW
        if n_win > 0:
            rms = np.sqrt(np.mean(region[: n_win * CUT_WINDOW].reshape(n_win, CUT_WINDOW) ** 2, axis=1))
            cut = search_from + int(np.argmin(rms)) * CUT_WINDOW + CUT_WINDOW // 2
        else:
            cut = hard_end
        ranges.append((start, cut))
        start = cut
    ranges.append((start, n))
    return ranges


def tokens_to_words(tokens: list[str], timestamps: list[float], offset: float,
                    chunk_end: float) -> list[Word]:
    """Group subword tokens into words. A token starting with a space opens a new word;
    punctuation and continuation pieces attach to the current one. Token timestamps are
    start times; a word ends where the next one starts (capped) or at the chunk end."""
    groups: list[list] = []   # [text, start]
    for tok, ts in zip(tokens, timestamps):
        if tok.startswith(" ") or not groups:
            groups.append([tok.strip(), ts])
        else:
            groups[-1][0] += tok
    # A leading "..." marks speech cut off at the chunk start (GigaAM); drop it. Other
    # punctuation-only pieces (dashes) stay as separate words: the diarized transcript
    # is rebuilt from words.
    if groups:
        groups[0][0] = groups[0][0].lstrip(".…")
    groups = [g for g in groups if g[0]]
    words = []
    for i, (text, ts) in enumerate(groups):
        start = offset + ts
        nxt = offset + groups[i + 1][1] if i + 1 < len(groups) else chunk_end
        end = min(nxt, start + MAX_WORD_SEC)
        words.append(Word(word=text, start=start, end=max(end, start + 0.02)))
    return words


ENGINES = ("whisper", "gigaam", "parakeet")


def create_engine(name: str, *, device: str, model_size: str, compute_type: str,
                  beam_size: int, asr_model: str | None = None) -> AsrEngine:
    if name == "whisper":
        return WhisperEngine(asr_model or model_size, device, compute_type, beam_size)
    if name in ONNX_PRESETS:
        return OnnxAsrEngine(name, asr_model)
    raise ValueError(f"Unknown ASR engine: {name} (choose from {', '.join(ENGINES)})")


def engine_label(name: str, asr_model: str | None = None) -> str:
    """Label for the 'Loading …' status line before the model exists."""
    if name in ONNX_PRESETS:
        default_model, label, _, _ = ONNX_PRESETS[name]
        return f"{label} ({asr_model or default_model}, int8)"
    return name
