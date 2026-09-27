"""Estimate the side of active speech from the two glasses sound sensors."""

from __future__ import annotations

import math
from collections import deque

import numpy as np


class SpeechDirection:
    """Compare speech-time rises above each sensor's own quiet-room floor."""

    def __init__(self, side_db: float, window_s: float = 0.4) -> None:
        self.side_db = side_db
        self.window_s = window_s
        self.samples: deque[tuple[float, float, float]] = deque()
        self.floor: tuple[float, float] | None = None
        self.quiet_samples = 0
        self.speaking = False

    def reset(self) -> None:
        """Forget sensor history after a pause, reply, or session reset."""
        self.samples.clear()
        self.floor = None
        self.quiet_samples = 0
        self.speaking = False

    def observe(self, t: float, left: float, right: float, motor_on: bool = False) -> None:
        """Keep recent unmixed levels; skip windows contaminated by the motor."""
        if motor_on:
            self.samples.clear()
            return
        if not all(math.isfinite(v) and 0 <= v <= 1023 for v in (left, right)):
            return
        if self.samples and (t <= self.samples[-1][0] or t - self.samples[-1][0] > 0.5):
            self.samples.clear()
        self.samples.append((t, left, right))
        while self.samples and t - self.samples[0][0] > self.window_s:
            self.samples.popleft()
        if not self.speaking:
            if self.floor is None:
                self.floor = (left, right)
            else:
                alpha = 0.15 if self.quiet_samples < 10 else 0.015
                self.floor = tuple(
                    (1 - alpha) * old + alpha * new
                    for old, new in zip(self.floor, (left, right), strict=True)
                )
            self.quiet_samples += 1

    def side(self, t: float, active: bool) -> str:
        """Return left, right, or none for VAD-confirmed speech at ``t``."""
        self.speaking = active
        if not active or self.floor is None or self.quiet_samples < 10:
            return "none"
        recent = [(left, right) for ts, left, right in self.samples if 0 <= t - ts <= self.window_s]
        if len(recent) < 3 or t - self.samples[-1][0] > 0.3:
            return "none"
        raised = np.maximum(np.asarray(recent) - self.floor, 0)
        left, right = np.percentile(raised, 75, axis=0)
        # A rise smaller than the configured side margin above the quiet floor
        # is too weak to localize, even if the other sensor is nearly silent.
        minimum = max(self.floor) * (10 ** (self.side_db / 20) - 1)
        if max(left, right) < minimum:
            return "none"
        ratio_db = 20 * math.log10((left + 1) / (right + 1))
        if ratio_db >= self.side_db:
            return "left"
        if ratio_db <= -self.side_db:
            return "right"
        return "none"
