"""Pure-tone T3/T4 detection in the high and low alarm bands."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class RhythmEvidence:
    tone_on: bool = False
    beeps: int = 0
    t3_cycles: int = 0
    t4_cycles: int = 0
    quiet_s: float = 0.0


class RhythmDetector:
    """Match alternating tone/silence runs, not arbitrary energy spikes."""

    def __init__(self, config: dict, rate: int = 32000):
        self.cfg, self.rate = config, rate
        self.size = round(rate * config["frame_s"])
        # Sinusoid projection avoids the 100 Hz FFT-bin leakage of a 10 ms window.
        times = np.arange(self.size) / rate
        frequencies = np.concatenate(
            [np.arange(a, b + 1, 10) for a, b in (config["low_band"], config["high_band"])]
        )
        self.basis = np.asarray(
            [
                np.linalg.qr(
                    np.column_stack((np.sin(2 * np.pi * f * times), np.cos(2 * np.pi * f * times)))
                )[0].T
                for f in frequencies
            ]
        )
        self.reset()

    def reset(self) -> None:
        self.pending = np.empty(0, np.float32)
        self.runs: deque = deque(maxlen=40)
        self.state = False
        self.length = 0
        self.noise = 0.0
        self.evidence = RhythmEvidence()

    def _near(self, actual: float, target: float) -> bool:
        return abs(actual - target) <= target * self.cfg["tolerance"] + self.cfg["frame_s"] / 2

    def _analyze(self) -> RhythmEvidence:
        duration = self.length * self.cfg["frame_s"]
        evidence = RhythmEvidence(tone_on=self.state, quiet_s=0.0 if self.state else duration)
        for kind, count in (("t3", 3), ("t4", 4)):
            on, gap, rest = [self.cfg[f"{kind}_{key}_s"] for key in ("on", "gap", "rest")]
            cycles, partial = 0, 0
            for state, completed_duration in self.runs:
                if state:
                    if self._near(completed_duration, on) and partial < count:
                        partial += 1
                    else:
                        cycles, partial = 0, 0
                elif partial:
                    if partial == count and self._near(completed_duration, rest):
                        cycles += 1
                        partial = 0
                    elif partial < count and self._near(completed_duration, gap):
                        pass
                    else:
                        # Completed gaps must fit both bounds. A short gap is not
                        # a growing pause once the next beep has already begun.
                        cycles, partial = 0, 0
            target = on if self.state else rest if partial == count else gap
            if duration > target * (1 + self.cfg["tolerance"]) + self.cfg["frame_s"] / 2:
                cycles, partial = 0, 0
            elif not self.state and partial == count:
                # The completed last beep confirms a group. Waiting for its
                # final rest adds five unnecessary seconds to the second T4.
                cycles += 1
            if kind == "t3":
                evidence.beeps = min(partial, count)
                evidence.t3_cycles = cycles
            else:
                evidence.t4_cycles = cycles
        return evidence

    def feed(self, samples: np.ndarray) -> RhythmEvidence:
        """Accept arbitrary block sizes; score each 10 ms frame once."""
        self.pending = np.concatenate((self.pending, samples))
        while len(self.pending) >= self.size:
            frame, self.pending = self.pending[: self.size], self.pending[self.size :]
            rms = float(np.sqrt(np.mean(frame * frame)))
            projections = np.einsum("fkn,n->fk", self.basis, frame)
            pure = float(np.max(np.sum(projections * projections, axis=1))) / max(
                float(np.dot(frame, frame)), 1e-12
            )
            tone = (
                rms >= max(self.cfg["min_rms"], self.noise * self.cfg["relative_energy"])
                and pure >= self.cfg["purity"]
            )
            if not tone:
                self.noise = 0.99 * self.noise + 0.01 * rms
            if tone == self.state:
                self.length += 1
            else:
                self.runs.append((self.state, self.length * self.cfg["frame_s"]))
                self.state, self.length = tone, 1
            self.evidence = self._analyze()
        return self.evidence
