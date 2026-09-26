"""Voice step of the enrollment station: a voice print from the laptop mic.

Section 2 - Audio (built by the enrollment stream). TODO: A-21.

The person reads a sentence shown on the phone into the laptop mic. Audio arrives as 16 kHz
blocks; every 32 ms frame (512 samples) gets a Silero speech probability (with the same
start/end hysteresis as captions) and a level. Voiced frames that are loud enough and not
clipped are kept, in memory, until there are `[voice] enroll_s` seconds of them; CAM++ then
turns them into the print and the audio is dropped.

The phone gets a level meter and one hint at a time, most important first:
"a bit softer" (clipping or too loud), "too noisy" (speech too close to the room's noise
floor), "speak up" (too quiet), "read the sentence aloud" (nothing heard yet), and
"keep talking" (they stopped before there was enough).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable

import numpy as np

FRAME = 512  # 32 ms at 16 kHz, Silero's frame
FRAME_S = FRAME / 16000


def db(x: float) -> float:
    return 20.0 * math.log10(max(float(x), 1e-9))


class VoiceStep:
    """Collects voiced speech and says how it sounds."""

    def __init__(
        self, settings, need_s: float, vad: Callable[[np.ndarray], float], start_t: float
    ) -> None:
        self.s = settings
        self.need_s = float(need_s)
        self.vad = vad
        self.opened_t = start_t
        self._pending = np.empty(0, np.float32)
        self._pending_t: float | None = None
        self.active = False  # inside speech (hysteresis)
        self.kept: list[np.ndarray] = []
        self.kept_frames = 0
        self.last_voiced_t: float | None = None
        self.first_voiced_t: float | None = None
        # recent per-frame stats: (t, rms_db, peak, voiced)
        self.recent: deque[tuple[float, float, float, bool]] = deque(maxlen=int(2.0 / FRAME_S))
        self.noise: deque[float] = deque(maxlen=int(3.0 / FRAME_S))
        self.now = start_t

    # ---------------------------------------------------------------- audio in
    def feed(self, t: float, samples: np.ndarray) -> None:
        samples = np.asarray(samples, np.float32)
        if self._pending_t is None or abs(self._pending_t + len(self._pending) / 16000 - t) > 0.05:
            self._pending, self._pending_t = np.empty(0, np.float32), t  # a gap: start over
        self._pending = np.concatenate((self._pending, samples))
        while len(self._pending) >= FRAME:
            frame, self._pending = self._pending[:FRAME], self._pending[FRAME:]
            ft = self._pending_t
            self._pending_t += FRAME_S
            self._frame(ft, frame)
        self.now = t + len(samples) / 16000

    def _frame(self, t: float, frame: np.ndarray) -> None:
        s = self.s
        prob = float(self.vad(frame))
        if not self.active and prob > s.vad_start:
            self.active = True
        elif self.active and prob < s.vad_end:
            self.active = False
        rms = float(np.sqrt(np.mean(frame * frame)))
        level, peak = db(rms), float(np.max(np.abs(frame)))
        self.recent.append((t, level, peak, self.active))
        if not self.active and prob < s.vad_end:
            self.noise.append(level)
            return
        if not self.active:
            return
        self.last_voiced_t = t
        if self.first_voiced_t is None:
            self.first_voiced_t = t
        if level >= s.quiet_db and peak < s.clip_level and not self.done:
            self.kept.append(frame.copy())
            self.kept_frames += 1

    # ---------------------------------------------------------------- state
    @property
    def voiced_s(self) -> float:
        return self.kept_frames * FRAME_S

    @property
    def done(self) -> bool:
        return self.voiced_s >= self.need_s

    def progress(self) -> float:
        return max(0.0, min(1.0, self.voiced_s / max(self.need_s, 1e-6)))

    def timed_out(self, t: float) -> bool:
        return t - self.opened_t >= self.s.voice_timeout_s

    def audio(self) -> np.ndarray:
        """The kept voiced speech (only ever held in memory)."""
        return np.concatenate(self.kept) if self.kept else np.empty(0, np.float32)

    def clear(self) -> None:
        self.kept.clear()
        self._pending = np.empty(0, np.float32)

    def noise_floor_db(self) -> float | None:
        if len(self.noise) < 8:
            return None
        return float(np.percentile(np.asarray(self.noise), 20))

    def hint(self) -> str:
        """The one thing to fix right now, or ""."""
        s, now = self.s, self.now
        last_s = [x for x in self.recent if x[0] >= now - 1.0]
        voiced = [x for x in last_s if x[3]]
        clipped = sum(1 for x in last_s if x[2] >= s.clip_level)
        if clipped >= 3 or any(x[1] > s.loud_db for x in voiced):
            return "a bit softer"
        speech = [x[1] for x in self.recent if x[3]]
        floor = self.noise_floor_db()
        if (
            len(speech) >= 8
            and floor is not None
            and float(np.median(speech)) - floor < s.min_snr_db
        ):
            return "too noisy"
        if len(voiced) >= 4 and float(np.median([x[1] for x in voiced])) < s.quiet_db:
            return "speak up"
        if self.done:
            return ""
        if self.first_voiced_t is None:
            return "read the sentence aloud" if now - self.opened_t >= 3.0 else ""
        if self.last_voiced_t is not None and now - self.last_voiced_t >= s.pause_hint_s:
            return "keep talking"
        return ""

    def level(self) -> dict:
        """The level meter: the loudest of the last ~100 ms, 0-1 over -60..0 dBFS."""
        now = self.now
        last = [x for x in self.recent if x[0] >= now - 0.1] or list(self.recent)[-1:]
        loud = max((x[1] for x in last), default=-120.0)
        peak = max((x[2] for x in last), default=0.0)
        return {
            "level": round(max(0.0, min(1.0, (loud + 60.0) / 60.0)), 3),
            "db": round(max(loud, -120.0), 1),
            "peak": round(peak, 3),
            "clipping": peak >= self.s.clip_level,
            "speech": bool(last and last[-1][3]),
            "noise_db": None if (f := self.noise_floor_db()) is None else round(f, 1),
            "voiced_s": round(self.voiced_s, 2),
            "need_s": self.need_s,
        }
