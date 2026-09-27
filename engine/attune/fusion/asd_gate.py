"""Light-ASD in who's talking: for a face with a fresh `asd_score`, its verdict decides.

Section 1 - Vision (fusion). TODO: V-22. Contracts: `Track.asd_score`.

`SpeakerFusion._assess` (V-21) judges each face's mouth every tick: moving, in time
with the sound, for long enough. Light-ASD (vision/asd.py) answers the same question
directly, from the mouth and the sound together, and far better when the room is full
of speech from people off camera. So each tick `decide` runs `SpeakerFusion.decide`
with every "covered" face (one with a fresh Light-ASD score) carrying Light-ASD's
verdict instead of `_assess`'s (only for that call; `_assess` keeps its own state):

- talking while the score is at least `asd_on`, until it drops below `asd_off`
  (hysteresis), never "probable";
- everything else in `decide` still applies to it: the voice veto (this utterance's
  voice matched someone else), the hold and switch rules, "You" first, and its own
  voice print letting a moving mouth speak;
- a face whose score is stale or missing (model off, face too small, too little
  history, vision too slow) keeps the lip-score checks, unchanged.

When Light-ASD says none of the visible faces is talking, `decide` finds no face and
the words go to off-screen or "Someone", however much a still mouth twitches; only a
face that earned this stretch of speech keeps it through a dip of up to
`asd_continuity_s` (V-31, `SpeakerFusion._continues`: a hand by the mouth). A covered
face that Light-ASD stops counts as a visible talker stopping (a turn), as in `_assess`.

Voice prints are still harvested only from a face that `decide` picks as talking and
whose lips are in time with the sound (`harvest_min_corr`): Light-ASD's verdict makes a
face talk, but a voice print needs both.

With `require_sync = false` (a film reel over unrelated audio) no face can be in time
with the sound, so Light-ASD, a sync check, is not used either.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..vision.settings import FusionSettings
from ..vision.types import Speaker, get


@dataclass
class _Face:
    score: float
    t: float  # frame time of the score
    talking: bool = False


class AsdGate:
    def __init__(self, settings: FusionSettings):
        self.s = settings
        self.faces: dict[int, _Face] = {}
        self._given: dict[int, bool] = {}  # the verdict each covered face got last tick

    def on_tracks(self, ev) -> None:
        """Keep each face's latest score and its talking state (hysteresis)."""
        t = float(get(ev, "t"))
        seen = set()
        for tr in get(ev, "tracks", []) or []:
            tid = int(get(tr, "track_id"))
            seen.add(tid)
            score = get(tr, "asd_score")
            if score is None:
                continue
            f = self.faces.get(tid)
            if f is None:
                f = self.faces[tid] = _Face(float(score), t)
            f.score, f.t = float(score), t
            f.talking = f.score >= (self.s.asd_off if f.talking else self.s.asd_on)
        for tid in [k for k in self.faces if k not in seen]:
            del self.faces[tid]

    def covered(self, fusion, now: float) -> dict[int, _Face]:
        """Visible faces whose Light-ASD score is fresh."""
        if not (self.s.asd_use and self.s.require_sync):
            return {}
        return {
            tid: f
            for tid, f in self.faces.items()
            if now - f.t <= self.s.asd_fresh_s
            and tid in fusion.tracks
            and now - fusion.tracks[tid].t <= 0.5
        }

    def decide(self, fusion, now: float) -> tuple[Speaker | None, float | None]:
        """`fusion.decide(now)`, with Light-ASD's verdict for the faces it covers."""
        covered = self.covered(fusion, now)
        speaking = fusion._speaking(now)
        saved = []
        for tid, f in covered.items():
            tr = fusion.tracks[tid]
            saved.append((tr, tr.talking, tr.probable))
            talking = f.talking and speaking
            if self._given.get(tid) and not talking:
                fusion._stopped[tid] = now  # a visible talker stopped: a turn
            self._given[tid] = talking
            tr.talking, tr.probable = talking, False
        for tid in [k for k in self._given if k not in covered]:
            del self._given[tid]
        try:
            return fusion.decide(now)
        finally:
            for tr, talking, probable in saved:
                tr.talking, tr.probable = talking, probable
