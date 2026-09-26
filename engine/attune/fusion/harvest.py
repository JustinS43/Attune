"""Voice-print harvesting for this session.

Section 1 - Vision (fusion). TODO: V-11. Plan: section 05 "Who's talking".

When a visible speaker has been confidently attributed (lips moving, in
time, solid tail) for `harvest_after_s` (1.5 s) of continuous speech, that
stretch is sent to Section 2 as `voice.harvest`, which adds it to the
person's voice print for the session. Later, when the same person talks
off-screen, their voice matches and case 4 can name them - strangers too.

Strangers have no person_id, so they're harvested under "track-<id>";
Section 2 echoes that id back in `audio.voice_match`. Voices heard while every
visible face is still are harvested under "offscreen-<n>" (V-19, see speaker.py).

A-21 (the glasses-mic refinement of a saved person's voice print): `FusionService` keeps a
`TalkerLog` of how many visible faces were talking on each tick, and every harvest carries
`talkers`, the most faces talking at once over its span; the audio side adapts a saved
person's print only from single-talker speech. `DelayedHarvests` holds each harvest for
`[voice] harvest_lag_s` before it is published, so the audio ring already holds the whole
span when the audio side reads it (without that, the newest part of the span was often not
there yet and the harvest failed with "audio span is no longer available").
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace

from ..vision.types import VoiceHarvest


def voice_id(person_id: str | None, track_id: int) -> str:
    return person_id or f"track-{track_id}"


class Harvester:
    def __init__(self, after_s: float = 1.5):
        self.after_s = after_s
        self._key: str | None = None
        self._start: float = 0.0

    def reset(self) -> None:
        self._key = None

    def update(self, now: float, confident_voice_id: str | None) -> VoiceHarvest | None:
        """Call on every fusion tick with the confidently attributed speaker (or None)."""
        if confident_voice_id is None:
            self._key = None
            return None
        if confident_voice_id != self._key:
            self._key = confident_voice_id
            self._start = now
            return None
        if now - self._start >= self.after_s:
            out = VoiceHarvest(confident_voice_id, self._start, now)
            self._start = now  # the next stretch starts here
            return out
        return None


class TalkerLog:
    """How many visible faces were talking at each tick, for the last `keep_s` seconds."""

    def __init__(self, keep_s: float = 30.0):
        self.keep_s = keep_s
        self._ticks: deque[tuple[float, int]] = deque()

    def note(self, t: float, talkers: int) -> None:
        self._ticks.append((t, int(talkers)))
        while self._ticks and t - self._ticks[0][0] > self.keep_s:
            self._ticks.popleft()

    def most_between(self, t0: float, t1: float) -> int:
        """The most faces talking at once on any tick in [t0, t1] (1 if no tick is logged)."""
        counts = [n for t, n in self._ticks if t0 <= t <= t1]
        return max(counts) if counts else 1

    def clear(self) -> None:
        self._ticks.clear()


class DelayedHarvests:
    """Harvests wait `lag_s` after their span ends, then leave with their `talkers` count."""

    def __init__(self, lag_s: float = 0.4):
        self.lag_s = lag_s
        self._waiting: deque[VoiceHarvest] = deque()

    def add(self, harvest: VoiceHarvest) -> None:
        self._waiting.append(harvest)

    def due(self, now: float, talkers: TalkerLog) -> list[VoiceHarvest]:
        out = []
        while self._waiting and now - self._waiting[0].t1 >= self.lag_s:
            h = self._waiting.popleft()
            out.append(replace(h, talkers=talkers.most_between(h.t0, h.t1)))
        return out

    def clear(self) -> None:
        self._waiting.clear()
