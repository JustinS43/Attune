"""Small time-windowed ring buffers.

Section 4 - Pages, Engine & Demo. TODO: P-02.

`TimedRing` keeps (t, value) pairs for the last `window_s` seconds (and at most
`maxlen` of them); the status strip uses it for rolling rates and averages.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class TimedRing:
    """Thread-safe (t, value) buffer that forgets entries older than `window_s`."""

    def __init__(self, window_s: float, maxlen: int = 4096) -> None:
        self.window_s = window_s
        self._items: deque[tuple[float, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, t: float, value: Any = None) -> None:
        with self._lock:
            self._items.append((t, value))
            self._trim(t)

    def _trim(self, now: float) -> None:
        while self._items and now - self._items[0][0] > self.window_s:
            self._items.popleft()

    def items(self, now: float | None = None) -> list[tuple[float, Any]]:
        with self._lock:
            if now is not None:
                self._trim(now)
            return list(self._items)

    def values(self, now: float | None = None) -> list[Any]:
        return [v for _, v in self.items(now)]

    def rate(self, now: float) -> float:
        """Entries per second over the window."""
        return len(self.items(now)) / self.window_s if self.window_s > 0 else 0.0

    def mean(self, now: float | None = None) -> float | None:
        vals = [v for v in self.values(now) if isinstance(v, (int, float))]
        return sum(vals) / len(vals) if vals else None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
