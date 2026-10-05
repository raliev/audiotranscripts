"""Optional structured event stream (JSON lines) consumed by the web UI.

Disabled unless main.py is started with --events PATH, so console mode is unchanged.
"""

import json
import threading
import time


class EventStream:
    def __init__(self):
        self._f = None
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self._f is not None

    def open(self, path):
        self._f = open(path, "a", encoding="utf-8")

    def emit(self, type_: str, **data):
        if self._f is None:
            return
        data["type"] = type_
        data["ts"] = time.time()
        line = json.dumps(data, ensure_ascii=False, default=str)
        with self._lock:
            try:
                self._f.write(line + "\n")
                self._f.flush()
            except Exception:
                pass

    def emit_throttled(self, key: str, interval: float, type_: str, **data):
        """Emit at most once per *interval* seconds for the given key."""
        if self._f is None:
            return
        now = time.monotonic()
        if now - self._last.get(key, 0.0) < interval:
            return
        self._last[key] = now
        self.emit(type_, **data)

    def close(self):
        with self._lock:
            if self._f is not None:
                self._f.close()
                self._f = None


events = EventStream()
