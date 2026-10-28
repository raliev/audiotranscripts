"""Spot the app's audio leaking into the mic (app+mic mode with speakers, no headphones).

The mic then hears the other side too, and their words come out a second time as "Me".
Such a mic phrase is a delayed, filtered copy of the app channel: its loudness rises and
falls with the app's. A phrase of your own doesn't follow the app's loudness, even when
both of you talk at once.
"""

import threading

import numpy as np

SAMPLE_RATE = 16000
FRAME = 320                 # 20 ms loudness frames
FRAME_SEC = FRAME / SAMPLE_RATE
LAGS = (-0.8, 1.0)          # how far the mic copy may lag the app's audio, s (output latency, clock drift)
CORR = 0.6                  # loudness correlation from which a mic phrase counts as the app's echo
FLOOR_DB = -60.0            # quieter than this counts as silence (the app channel is digital zeros then)
ACTIVE_DB = -50.0           # the app is playing something in a frame this loud
MIN_ACTIVE = 0.2            # share of the phrase the app must be playing for it to be an echo at all
OWN_DB = 10.0               # a mic frame this much louder than the app's leak explains is your own voice
MAX_OWN = 0.15              # share of the phrase's loud frames that may be such (else it's yours, talking over them)


def loudness_db(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // FRAME
    if not n:
        return np.zeros(0)
    frames = audio[: n * FRAME].reshape(n, FRAME).astype(np.float64)
    return np.maximum(10 * np.log10(np.mean(frames * frames, axis=1) + 1e-12), FLOOR_DB)


class AppLoudness:
    """The app channel's loudness on the session clock, for the last few minutes."""

    def __init__(self, seconds: float = 300):
        self.db = np.full(int(seconds / FRAME_SEC), FLOOR_DB)
        self.written = 0                    # frames so far; frame k starts at t0 + k * FRAME_SEC
        self.t0: float | None = None        # session time of the app channel's first sample
        self.rest = np.zeros(0, np.float32)
        self.lock = threading.Lock()

    def add(self, data: np.ndarray, t0: float):
        with self.lock:
            if self.t0 is None:
                self.t0 = t0
            data = np.concatenate([self.rest, data])
            n = len(data) // FRAME
            self.rest = data[n * FRAME:]
            size = len(self.db)
            for v in loudness_db(data[: n * FRAME]):
                self.db[self.written % size] = v
                self.written += 1

    def window(self, start: int, n: int) -> np.ndarray | None:
        """Frames start .. start+n-1 (NaN where not kept), or None if none are kept."""
        with self.lock:
            lo, hi = max(start, self.written - len(self.db), 0), min(start + n, self.written)
            if hi <= lo:
                return None
            out = np.full(n, np.nan)
            out[lo - start: hi - start] = self.db[np.arange(lo, hi) % len(self.db)]
            return out

    def is_echo(self, mic_audio: np.ndarray, t_start: float) -> tuple[bool, float]:
        """Whether the mic phrase starting at session time t_start is only the app's audio
        heard through the speakers, and how closely its loudness follows the app's."""
        mic = loudness_db(mic_audio)
        if len(mic) < 10 or self.t0 is None or np.ptp(mic) < 1e-6:
            return False, 0.0
        lag_lo, lag_hi = int(LAGS[0] / FRAME_SEC), int(LAGS[1] / FRAME_SEC)
        # App frames from (t_start - max lag) to (phrase end - min lag)
        first = int(round((t_start - self.t0) / FRAME_SEC)) - lag_hi
        app = self.window(first, len(mic) + lag_hi - lag_lo)
        if app is None:
            return False, 0.0
        best, best_a, best_m = 0.0, None, None
        for lag in range(lag_lo, lag_hi + 1):
            a = app[lag_hi - lag: lag_hi - lag + len(mic)]
            ok = ~np.isnan(a)
            if ok.sum() < 0.7 * len(mic):
                continue
            a, m = a[ok], mic[ok]
            if np.mean(a > ACTIVE_DB) < MIN_ACTIVE or np.ptp(a) < 1e-6 or np.ptp(m) < 1e-6:
                continue
            r = float(np.corrcoef(a, m)[0, 1])
            if r > best:
                best, best_a, best_m = r, a, m
        if best < CORR:
            return False, best
        # The leak is the app's loudness plus a fixed gain; your voice on top of it is louder than that.
        both = best_a > ACTIVE_DB
        gain = np.median(best_m[both] - best_a[both])
        loud = best_m >= best_m.max() - 25
        own = np.mean(best_m[loud] - np.maximum(best_a[loud], FLOOR_DB) - gain > OWN_DB)
        return bool(own <= MAX_OWN), best
