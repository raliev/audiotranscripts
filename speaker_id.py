"""Speaker embedding and diarization utilities."""

import sys
import threading
from dataclasses import dataclass, field

import numpy as np


@dataclass
class WordToken:
    text: str
    t_start: float
    t_end: float
    speaker: int = 0


@dataclass
class AsrSegment:
    t_start: float
    t_end: float
    words: list = field(default_factory=list)


@dataclass
class EmbeddingWindow:
    vector: np.ndarray
    t_start: float
    t_end: float


class SpeakerEmbedder:
    """Compute speaker embeddings using SpeechBrain ECAPA-TDNN (192-dim)."""

    def __init__(self, device="cpu"):
        self.lock = threading.Lock()    # used by the embedding worker and live labelling at once
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError:
            from speechbrain.pretrained import EncoderClassifier
        import downloads
        extra = {}
        if sys.platform == "win32":   # symlinks into the HF cache need admin / developer mode there
            try:
                from speechbrain.utils.fetching import LocalStrategy
                extra["local_strategy"] = LocalStrategy.COPY
            except ImportError:
                pass
        with downloads.watch_hf("speechbrain/spkrec-ecapa-voxceleb", "Speaker model (ECAPA)"):
            self.model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                run_opts={"device": device},
                **extra,
            )

    def embed(self, audio: np.ndarray) -> np.ndarray:
        """Return L2-normalised 192-dim embedding from float32 mono audio."""
        import torch

        tensor = torch.from_numpy(audio).unsqueeze(0)
        with self.lock, torch.no_grad():
            embedding = self.model.encode_batch(tensor)
        vec = embedding.squeeze().cpu().numpy()
        vec = vec / (np.linalg.norm(vec) + 1e-8)
        return vec.astype(np.float32)


MIN_CLUSTER_SEC = 1.0   # shorter phrases give too rough a voice print to form speakers; they join the closest
MIN_SPEAKER_SEC = 8.0   # a "speaker" with less speech is mostly stray phrases of the others (threshold mode)


class LiveSpeakers:
    """Speaker numbers while recording, a first guess: a phrase joins the voice whose phrases
    it is closest to on average (the distance the final clustering uses) or starts a new one.
    A voice gets its number once it has MIN_SPEAKER_SEC of speech; until then (None) the line
    keeps its plain label. The end of the session re-clusters everything word by word."""

    def __init__(self, embedder, threshold=0.7, num_speakers=None):
        self.embedder, self.threshold, self.num_speakers = embedder, threshold, num_speakers
        self.sums: list[np.ndarray] = []    # per voice: sum of the phrase vectors
        self.counts: list[int] = []
        self.speech: list[float] = []       # seconds of speech
        self.numbers: dict[int, int] = {}   # voice → speaker number, once it has enough speech

    def label(self, audio: np.ndarray, speech_sec: float) -> int | None:
        vec = self.embedder.embed(audio)
        dist = [1 - float(vec @ s) / n for s, n in zip(self.sums, self.counts)]
        if speech_sec < MIN_CLUSTER_SEC:
            known = [i for i in self.numbers]
            return self.numbers[min(known, key=lambda i: dist[i])] if known else None
        best = int(np.argmin(dist)) if dist else -1
        full = self.num_speakers is not None and len(self.numbers) >= self.num_speakers
        if full:
            best = min(self.numbers, key=lambda i: dist[i])
        elif best < 0 or dist[best] > self.threshold:
            self.sums.append(np.zeros_like(vec))
            self.counts.append(0)
            self.speech.append(0.0)
            best = len(self.sums) - 1
        self.sums[best] += vec
        self.counts[best] += 1
        self.speech[best] += speech_sec
        if best not in self.numbers and (self.speech[best] >= MIN_SPEAKER_SEC or self.num_speakers):
            self.numbers[best] = len(self.numbers) + 1
        return self.numbers.get(best)


def cluster_and_label(windows, num_speakers=None, threshold=0.7):
    """Cluster embedding windows; return 1-indexed labels ordered by first appearance.
    Only phrases of MIN_CLUSTER_SEC+ form the speakers, and (threshold mode) only voices with
    MIN_SPEAKER_SEC+ of speech stay speakers: the rest go to the closest speaker. Without
    this, short phrases over a call's codec split each person into dozens of "speakers"."""
    if not windows:
        return []
    if len(windows) == 1:
        return [1]

    from sklearn.cluster import AgglomerativeClustering

    vectors = np.array([w.vector for w in windows])
    dur = np.array([w.t_end - w.t_start for w in windows])
    core = np.flatnonzero(dur >= MIN_CLUSTER_SEC)
    if len(core) < 2:
        core = np.arange(len(windows))

    if num_speakers is not None:
        clustering = AgglomerativeClustering(
            n_clusters=min(num_speakers, len(core)), metric="cosine", linkage="average",
        )
    else:
        clustering = AgglomerativeClustering(
            n_clusters=None, distance_threshold=threshold,
            metric="cosine", linkage="average",
        )
    core_labels = clustering.fit_predict(vectors[core])

    keep = np.unique(core_labels)
    if num_speakers is None:
        speech = np.bincount(core_labels, weights=dur[core])
        big = np.flatnonzero(speech >= MIN_SPEAKER_SEC)
        keep = big if len(big) else [int(np.argmax(speech))]

    # Every other window (short, or from a dropped voice) goes to the closest kept speaker
    centroids = np.array([vectors[core[core_labels == k]].mean(axis=0) for k in keep])
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True) + 1e-8
    raw_labels = np.asarray(keep)[np.argmax(vectors @ centroids.T, axis=1)]
    kept = np.isin(core_labels, keep)
    raw_labels[core[kept]] = core_labels[kept]

    # Renumber by first appearance in time
    first_time = {}
    for i, w in enumerate(windows):
        lbl = raw_labels[i]
        if lbl not in first_time:
            first_time[lbl] = w.t_start
    sorted_labels = sorted(first_time, key=lambda l: first_time[l])
    label_map = {old: new + 1 for new, old in enumerate(sorted_labels)}

    return [label_map[l] for l in raw_labels]


def speaker_for_time(t, windows, labels):
    """Determine speaker at time *t* by majority vote of covering windows."""
    covering = [labels[i] for i, w in enumerate(windows)
                if w.t_start <= t <= w.t_end]
    if covering:
        return max(set(covering), key=covering.count)
    # Fallback: nearest window
    nearest = min(range(len(windows)),
                  key=lambda i: min(abs(t - windows[i].t_start),
                                    abs(t - windows[i].t_end)))
    return labels[nearest]


def diarize_words(segments, windows, labels):
    """Assign speaker labels to every WordToken in *segments* (in-place)."""
    for seg in segments:
        for word in seg.words:
            t_mid = (word.t_start + word.t_end) / 2
            word.speaker = speaker_for_time(t_mid, windows, labels)
