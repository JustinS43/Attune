"""In-time check: do this face's lips move with the sound?

Section 1 - Vision (fusion). TODO: V-10. Plan: section 05 "Who's talking".

Talking moves the lips with the syllables; smiling, nodding and chewing
don't. Over the last second, the mouth-open ratio and the loudness envelope
are resampled to 25 Hz and correlated, allowing a small lag around the
calibrated audio/video offset. A correlation >= `sync_min_corr` counts as in
time.

The loudness comes from the microphone (Section 2's 16 kHz `audio.block`
PCM, turned into a 20 ms dB envelope here) when it exists, otherwise from the glasses' sound sensors (`sensors.levels`, every
50 ms). With neither, the check returns None and the caller treats it as
"can't tell" rather than "out of time".
"""

from __future__ import annotations

from collections import deque

import numpy as np

RATE_HZ = 25.0


class Envelope:
    """Recent loudness samples (t, dB) from whichever source is available."""

    def __init__(self, keep_s: float = 3.0):
        self.keep_s = keep_s
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, t: float, db: float) -> None:
        if self.samples and t <= self.samples[-1][0]:
            return
        self.samples.append((t, db))
        while self.samples and t - self.samples[0][0] > self.keep_s:
            self.samples.popleft()

    def clear(self) -> None:
        self.samples.clear()


def _resample(samples, t0: float, t1: float) -> np.ndarray | None:
    pts = [(t, v) for t, v in samples if t0 - 0.2 <= t <= t1 + 0.2]
    if len(pts) < 5:
        return None
    ts, vs = np.array(pts).T
    if ts[0] > t0 + 0.25 or ts[-1] < t1 - 0.25:  # must cover most of the window
        return None
    grid = np.arange(t0, t1, 1.0 / RATE_HZ)
    return np.interp(grid, ts, vs)


def in_time_score(
    mouth: list[tuple[float, float]] | deque,
    envelope: Envelope,
    now: float,
    offset_s: float = 0.0,
    window_s: float = 1.0,
    max_lag_s: float = 0.12,
) -> float | None:
    """Best correlation between lips and sound over the last window, or None if there's no data.

    `offset_s` is how much the audio lags the video (from the clap test).
    """
    t1 = now - max_lag_s - abs(offset_s)  # leave room to shift either way
    t0 = t1 - window_s
    lips = _resample(mouth, t0, t1)
    if lips is None or lips.std() < 1e-4:
        return None
    best = None
    for lag in np.arange(-max_lag_s, max_lag_s + 1e-9, 1.0 / RATE_HZ):
        sound = _resample(envelope.samples, t0 + offset_s + lag, t1 + offset_s + lag)
        if sound is None:
            continue
        n = min(len(sound), len(lips))
        if n < 10:
            continue
        a, b = lips[:n], sound[:n]
        r = 0.0 if b.std() < 1e-4 or a.std() < 1e-4 else float(np.corrcoef(a, b)[0, 1])
        best = r if best is None else max(best, r)
    return best
