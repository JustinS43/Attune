"""Light-ASD in who's talking: when a face has a fresh `asd_score`, it decides for that face.

Section 1 - Vision (fusion). TODO: V-22. Contracts: `Track.asd_score`.

A visible face with a fresh Light-ASD score is "covered": it can be the speaker only
while Light-ASD says it is talking, with hysteresis (on at `asd_on`, off again below
`asd_off`). Faces without a score (model off or missing, face too small, too little
history, score stale) keep the lip-score rules of `SpeakerFusion.decide`, unchanged.

Each tick:
1. `decide` runs with the covered faces hidden, so it answers exactly as if they
   weren't there: "You", a lip-score face among the uncovered ones, or the off-screen
   / "Someone" answer (voice match, exit side, sensor side).
2. "You" stands. Otherwise a covered face that Light-ASD says is talking is the
   speaker: the highest score, but the current speaker is kept for `hold_s` and
   until another face beats it by `asd_switch_margin`.
3. With no covered face talking, step 1's answer stands. So when Light-ASD says
   none of the visible faces is talking, the words go to off-screen or "Someone",
   however much a still mouth twitches.
With no covered faces at all this is exactly `decide`.

The in-time value reported for a Light-ASD face (it gates voice-print harvesting) is
1.0 at `asd_harvest` or more, otherwise 0.0.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..vision.settings import FusionSettings
from ..vision.types import Speaker, get


@dataclass
class _Face:
    score: float
    t: float
    talking: bool = False
    on_t: float = -1e9  # last time it was talking


class AsdGate:
    def __init__(self, settings: FusionSettings):
        self.s = settings
        self.faces: dict[int, _Face] = {}

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
            if f.talking:
                f.on_t = t
        for tid in [k for k in self.faces if k not in seen]:
            del self.faces[tid]

    def covered(self, fusion, now: float) -> dict[int, _Face]:
        """Visible faces whose Light-ASD score is fresh."""
        if not self.s.asd_use:
            return {}
        return {
            tid: f
            for tid, f in self.faces.items()
            if now - f.t <= self.s.asd_fresh_s
            and tid in fusion.tracks
            and now - fusion.tracks[tid].t <= 0.5
        }

    def decide(self, fusion, now: float) -> tuple[Speaker | None, float | None]:
        """`fusion.decide(now)`, with Light-ASD deciding for the faces it covers."""
        cov = self.covered(fusion, now)
        if not cov:
            return fusion.decide(now)
        hidden = {tid: fusion.tracks.pop(tid) for tid in cov}
        try:
            base, r = fusion.decide(now)
        finally:
            fusion.tracks.update(hidden)
        if base is None or base.kind == "you":
            return base, r
        labels = fusion._labels()
        if base.kind in ("face", "probable_face") and base.track_id in labels:
            base = replace(base, label=labels[base.track_id])  # numbered among all faces

        cur = fusion.current
        talking = {tid: f for tid, f in cov.items() if f.talking}
        if (
            cur is not None
            and cur.kind == "face"
            and cur.track_id in cov
            and cur.track_id not in talking
            and now - cov[cur.track_id].on_t <= self.s.hold_s
        ):
            talking[cur.track_id] = cov[cur.track_id]  # a pause between words
        if not talking:
            return base, r
        tid = max(talking, key=lambda k: talking[k].score)
        if cur is not None and cur.kind == "face" and cur.track_id in talking:
            held = talking[cur.track_id].score
            if (
                now - fusion._current_since < self.s.hold_s
                or talking[tid].score < held + self.s.asd_switch_margin
            ):
                tid = cur.track_id
        info = fusion.tracks[tid]
        in_time = 1.0 if talking[tid].score >= self.s.asd_harvest else 0.0
        return Speaker("face", tid, info.person_id, labels[tid]), in_time
