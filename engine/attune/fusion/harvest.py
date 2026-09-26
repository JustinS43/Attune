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
"""

from __future__ import annotations

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
