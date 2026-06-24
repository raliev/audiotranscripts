"""Speaker embedding and diarization utilities."""

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
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError:
            from speechbrain.pretrained import EncoderClassifier
        self.model = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            run_opts={"device": device},
        )

    def embed(self, audio: np.ndarray) -> np.ndarray:
        """Return L2-normalised 192-dim embedding from float32 mono audio."""
        import torch

        tensor = torch.from_numpy(audio).unsqueeze(0)
        with torch.no_grad():
            embedding = self.model.encode_batch(tensor)
        vec = embedding.squeeze().cpu().numpy()
        vec = vec / (np.linalg.norm(vec) + 1e-8)
        return vec.astype(np.float32)


def cluster_and_label(windows, num_speakers=None, threshold=0.6):
    """Cluster embedding windows; return 1-indexed labels ordered by first appearance."""
    if not windows:
        return []
    if len(windows) == 1:
        return [1]

    from sklearn.cluster import AgglomerativeClustering

    vectors = np.array([w.vector for w in windows])

    if num_speakers is not None:
        n = min(num_speakers, len(windows))
        clustering = AgglomerativeClustering(
            n_clusters=n, metric="cosine", linkage="average",
        )
    else:
        clustering = AgglomerativeClustering(
            n_clusters=None, distance_threshold=threshold,
            metric="cosine", linkage="average",
        )

    raw_labels = clustering.fit_predict(vectors)

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
