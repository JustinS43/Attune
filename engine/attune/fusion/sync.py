"""In-time check: do this face's lips move with the sound?

Section 1 - Vision (fusion). TODO: V-10. Plan: section 05 "Who's talking".

Talking moves the lips with the syllables; smiling, nodding and chewing
don't. Over the last `av_window_s` (1.5 s), the mouth-open ratio and the
loudness envelope are resampled to 25 Hz, both band-passed to syllable rates
(about 2.5-8 Hz, `vision.mouth.band_pass`) and correlated, allowing a small lag
around the calibrated audio/video offset. A correlation >= `sync_min_corr`
counts as in time.

V-19: the band-pass keeps a slow mouth movement (lips parting, a yawn) that
happens to rise with the room's loudness from counting as "in time"; on a live
clip of a silent face those reached r >= 0.3 in about 70% of 1 s windows
unfiltered.

The loudness comes from the microphone (Section 2's 16 kHz `audio.block`
PCM, turned into a 20 ms dB envelope here) when it exists, otherwise from the glasses' sound sensors (`sensors.levels`, every
50 ms). With neither, the check returns None and the caller treats it as
"can't tell" rather than "out of time".
"""

from __future__ import annotations

from collections import deque

import numpy as np

from ..vision.mouth import band_pass

RATE_HZ = 25.0
# Quieter than this is silence. Digital silence (a dropped audio block, a mic's noise gate)
# reads -180 dB and would otherwise outweigh every syllable in the correlation.
FLOOR_DB = -70.0
QUIET_PCT = 20.0
PRE_S = 0.4  # extra history the band-pass filter needs before the window


class Envelope:
    """Recent loudness samples (t, dB) from whichever source is available."""

    def __init__(self, keep_s: float = 3.0):
        self.keep_s = keep_s
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, t: float, db: float) -> None:
        if self.samples and t <= self.samples[-1][0]:
            return
        self.samples.append((t, max(float(db), FLOOR_DB)))
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


def _filtered(samples, t0: float, t1: float, band: bool, quiet: bool = False) -> np.ndarray | None:
    """Samples over [t0, t1] on the 25 Hz grid, band-passed if `band`.

    `quiet` (loudness): everything quieter than the window's QUIET_PCT percentile counts as
    that level, so a pause, a mic's noise gate and a dropped block all read as one silence
    instead of the deepest of them outweighing the syllables.
    """
    if not band:
        return _resample(samples, t0, t1)
    pre = round(PRE_S * RATE_HZ)
    v = _resample(samples, t0 - PRE_S, t1)
    if v is None:
        pre = 0
        v = _resample(samples, t0, t1)  # a new face: no history before the window yet
        if v is None:
            return None
    if quiet:
        v = np.maximum(v, np.percentile(v, QUIET_PCT))
    return band_pass(v, RATE_HZ)[pre:]


def in_time_score(
    mouth: list[tuple[float, float]] | deque,
    envelope: Envelope,
    now: float,
    offset_s: float = 0.0,
    window_s: float = 1.5,
    max_lag_s: float = 0.12,
    band: bool = True,
    min_window_s: float = 0.8,
) -> float | None:
    """Best correlation between lips and sound over the last window, or None if there's no data.

    `offset_s` is how much the audio lags the video (from the clap test). `band`
    band-passes both to syllable rates first (V-19). A face seen for less than
    `window_s` is checked over what there is, if that's at least `min_window_s`.
    """
    t1 = now - max_lag_s - abs(offset_s)  # leave room to shift either way
    t0 = t1 - window_s
    if mouth and mouth[0][0] > t0:
        t0 = mouth[0][0]
        if t1 - t0 < min_window_s:
            return None
    lips = _filtered(mouth, t0, t1, band)
    if lips is None or lips.std() < 1e-4:
        return None
    best = None
    for lag in np.arange(-max_lag_s, max_lag_s + 1e-9, 1.0 / RATE_HZ):
        sound = _filtered(
            envelope.samples, t0 + offset_s + lag, t1 + offset_s + lag, band, quiet=True
        )
        if sound is None:
            continue
        n = min(len(sound), len(lips))
        if n < 10:
            continue
        a, b = lips[:n], sound[:n]
        r = 0.0 if b.std() < 1e-4 or a.std() < 1e-4 else float(np.corrcoef(a, b)[0, 1])
        best = r if best is None else max(best, r)
    return best
