"""Decides who is talking, gives captions their speaker, and builds the scene.

Section 1 - Vision (fusion). TODO: V-09. Plan: section 05 "Who's talking".

While speech is detected, each tick (15 per second) checks, in order:
1. You: both glasses sensors are above the calibrated own-voice level and
   within 3 dB of each other.
2. A visible speaker: lip score >= 0.03 and lips in time with the sound.
   Held at least 0.5 s; only switches to someone scoring 1.5x higher.
3. A probable visible speaker: exactly one face in the 0.015-0.03 band while
   everyone else is still. Dashed tail.
4. Off-screen: after ~1 s of speech, a voice match >= 0.5 names the speaker;
   the side comes from where they left the frame (< 30 s ago), otherwise
   from the louder sound sensor (>= 3 dB). Until then: "Someone".

Captions: every word has a time, so a transcript is split where the speaker
changes. If nobody qualifies yet, the first words wait up to 300 ms for a
speaker before showing as "Someone". Once a segment is shown, later drafts keep
its words and speaker unless the evidence over most of it changes (see
`_redraft`); a face leaving never re-labels what it already said, and segment
ids that drop out are retracted (`caption.retract`).

This class is pure logic with an explicit `now`, so tests can drive it with
simulated events; FusionService (service.py) runs it on the bus.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np

from ..vision.settings import FusionSettings
from ..vision.types import (
    Caption,
    CaptionRetract,
    FaceState,
    Offscreen,
    Scene,
    Speaker,
    VoiceHarvest,
    get,
)
from .asd_gate import AsdGate
from .harvest import Harvester, voice_id
from .sync import Envelope, in_time_score


@dataclass
class _TrackInfo:
    track_id: int
    box: list[float]
    lip_score: float
    person_id: str | None
    name: str | None
    status: str
    t: float
    mouth: deque = field(default_factory=lambda: deque(maxlen=150))


@dataclass
class _Pending:
    event: object
    first_seen: float


@dataclass
class _Seg:
    """A caption segment already sent: its id, speaker and word span."""

    uid: str
    speaker: Speaker
    t_lo: float
    t_hi: float


@dataclass
class _UttMemory:
    """What has been shown of an unfinished utterance."""

    segs: list[_Seg] = field(default_factory=list)
    sent: set[str] = field(default_factory=set)
    next_index: int = 1  # the next ".n" for a new segment; retracted numbers are not reused
    t: float = 0.0


@dataclass
class _Group:
    """A run of words with one speaker while a caption is being split."""

    speaker: Speaker
    words: list
    prev: list[str] = field(default_factory=list)  # ids of the shown segments it holds
    locked: bool = False  # already shown: a known speaker here is not smoothed away
    evidence: list = field(default_factory=list)  # the timeline's speaker per word
    shown: bool = False  # holds words already shown in another segment


def _db(level: float) -> float:
    return 20.0 * math.log10(max(float(level), 1.0))


def _same(a: Speaker | None, b: Speaker | None) -> bool:
    if a is None or b is None:
        return a is b
    return (a.kind, a.track_id, a.person_id, a.label, a.side) == (
        b.kind,
        b.track_id,
        b.person_id,
        b.label,
        b.side,
    )


def _who(s: Speaker | None) -> tuple:
    """Who a speaker is, ignoring label wording and a Someone's side (as the pages group them)."""
    if s is None or s.kind == "someone":
        return ("someone",)
    if s.kind in ("face", "probable_face"):
        return ("face", s.track_id)
    if s.kind == "offscreen":
        return ("offscreen", s.person_id or s.label)
    return (s.kind,)


def _someone() -> Speaker:
    return Speaker("someone", label="Someone", side="none")


def _mid(w) -> float:
    return (float(w[1]) + float(w[2])) / 2


def _end(w, cap: float) -> float:
    """A word's end, counting at most `cap` seconds of it.

    The streaming recogniser stretches a draft's last word to the end of its audio
    chunk (a 0.2 s word can read 0.9 s until the next draft), so word lengths are
    capped wherever they decide how long a piece of speech is.
    """
    return min(float(w[2]), float(w[1]) + cap)


def _dur(w, cap: float = 1e9) -> float:
    return max(_end(w, cap) - float(w[1]), 0.05)


_RANK = {"face": 1}


def _better(a: Speaker | None, b: Speaker) -> Speaker:
    """Of two speakers for the same person, the more certain one (the later on a tie)."""
    if a is None or _RANK.get(b.kind, 0) >= _RANK.get(a.kind, 0):
        return b
    return a


def _refresh(cur: Speaker, new: Speaker) -> Speaker:
    """`cur` with the newer label of the same person; never downgraded, a Someone's side kept."""
    if _who(cur) != _who(new):
        return new
    kind = "face" if "face" in (cur.kind, new.kind) else cur.kind
    side = cur.side if cur.kind in ("someone", "offscreen") else new.side
    return Speaker(
        kind,
        cur.track_id if cur.track_id is not None else new.track_id,
        new.person_id or cur.person_id,
        new.label or cur.label,
        side,
    )


def _raw_groups(pairs: list) -> list[_Group]:
    """Split (word, speaker) pairs where the speaker changes."""
    groups: list[_Group] = []
    for w, spk in pairs:
        if groups and _who(groups[-1].speaker) == _who(spk):
            g = groups[-1]
            g.speaker = _better(g.speaker, spk)
            g.words.append(w)
            g.evidence.append(spk)
        else:
            groups.append(_Group(spk, [w], [], False, [spk]))
    for g in groups:
        if g.speaker.kind == "someone":  # the side most of its words came from
            sides: dict[str, float] = {}
            for w, spk in zip(g.words, g.evidence):
                sides[spk.side] = sides.get(spk.side, 0.0) + _dur(w)
            g.speaker = Speaker("someone", label="Someone", side=max(sides, key=sides.get))
    return groups


def _merge_neighbours(groups: list[_Group]) -> list[_Group]:
    """Join neighbouring groups of the same person."""
    out: list[_Group] = []
    for g in groups:
        if out and _who(out[-1].speaker) == _who(g.speaker):
            a = out[-1]
            out[-1] = _Group(
                _refresh(a.speaker, g.speaker) if g.speaker.kind != "someone" else a.speaker,
                a.words + g.words,
                a.prev + g.prev,
                a.locked or g.locked,
                a.evidence + g.evidence,
                a.shown or g.shown,
            )
        else:
            out.append(g)
    return out


class SpeakerFusion:
    def __init__(self, settings: FusionSettings | None = None):
        self.s = settings or FusionSettings()
        self.asd_gate = AsdGate(self.s)  # V-22: Light-ASD decides for the faces it scores
        self.tracks: dict[int, _TrackInfo] = {}
        self.frame_no = 0
        self.frame_t = 0.0
        self.envelope = Envelope()
        self._level_src_t = -1e9  # last audio.level sample; sensors are only the fallback
        self.levels: tuple[float, float, float] | None = None  # (t, left dB, right dB)
        self.is_speech = False
        self.speech_start: float | None = None
        self.last_speech_t = -1e9
        self.voice_matches: deque = deque(maxlen=50)  # (t, utt_id, person_id, score)
        self.exits: dict[str, tuple[str, float]] = {}  # voice id -> (side, t)
        self.voice_labels: dict[str, str] = {}  # voice id -> label
        self.colors: dict[int, str] = {}
        self.descriptions: dict[int, str] = {}
        self.proposals: dict[int, str] = {}  # track_id -> proposed name
        self.current: Speaker | None = None
        self._current_since = 0.0
        self._current_score = 0.0
        self._current_in_time: float | None = None
        self.timeline: deque[tuple[float, Speaker | None]] = deque()
        self.pending: dict[str, _Pending] = {}
        self.utts: dict[str, _UttMemory] = {}  # unfinished utterances already shown
        self.retractions: list[CaptionRetract] = []
        self.harvester = Harvester(self.s.harvest_after_s)

    # ---------------- inputs ----------------
    def on_tracks(self, ev) -> None:
        self.asd_gate.on_tracks(ev)
        t = float(get(ev, "t"))
        self.frame_no = int(get(ev, "frame_no", 0))
        self.frame_t = t
        seen = set()
        for tr in get(ev, "tracks", []):
            tid = int(get(tr, "track_id"))
            seen.add(tid)
            info = self.tracks.get(tid)
            if info is None:
                info = self.tracks[tid] = _TrackInfo(tid, [], 0.0, None, None, "unknown", t)
            info.box = list(get(tr, "box"))
            info.lip_score = float(get(tr, "lip_score", 0.0))
            info.person_id = get(tr, "person_id")
            info.name = get(tr, "name")
            info.status = get(tr, "status", "unknown")
            info.t = t
            mouth = get(tr, "mouth_open")
            if mouth is not None:
                info.mouth.append((t, float(mouth)))
            self.voice_labels[voice_id(info.person_id, tid)] = self.label_for(info)
        for tid in [k for k in self.tracks if k not in seen]:
            del self.tracks[tid]

    def on_track_lost(self, ev) -> None:
        tid = int(get(ev, "track_id"))
        side = get(ev, "side", "none")
        t = float(get(ev, "t"))
        info = self.tracks.pop(tid, None)
        self.exits[f"track-{tid}"] = (side, t)
        if info is not None and info.person_id:
            self.exits[info.person_id] = (side, t)

    def on_vad(self, ev) -> None:
        t = float(get(ev, "t"))
        speech = bool(get(ev, "is_speech"))
        if speech:
            if not self.is_speech and t - self.last_speech_t > self.s.speech_hangover_s:
                self.speech_start = t
            self.last_speech_t = t
        self.is_speech = speech

    def on_audio_level(self, ev) -> None:
        t = float(get(ev, "t"))
        self._level_src_t = t
        self.envelope.add(t, float(get(ev, "db")))

    def on_audio_block(self, ev) -> None:
        """Loudness envelope from Section 2's 16 kHz PCM blocks (20 ms steps, dBFS)."""
        if int(get(ev, "sample_rate", 0)) != 16000:
            return  # the 32 kHz stream is for alerts; one stream is enough
        samples = np.asarray(get(ev, "samples"), np.float32)
        t0 = float(get(ev, "t"))
        step = 320
        for i in range(0, len(samples), step):
            chunk = samples[i : i + step]
            if len(chunk) < step // 4:
                break
            rms = float(np.sqrt(np.mean(chunk * chunk)))
            self.on_audio_level({"t": t0 + i / 16000, "db": 20 * np.log10(rms + 1e-9)})

    def on_sensor_levels(self, ev) -> None:
        t = float(get(ev, "t"))
        left, right = _db(get(ev, "left", 0)), _db(get(ev, "right", 0))
        if get(ev, "motor_on", False):
            return  # our own buzz, not sound in the room
        self.levels = (t, left, right)
        if t - self._level_src_t > 1.0:  # no mic envelope: use the sensors
            self.envelope.add(t, max(left, right))

    def on_voice_match(self, ev) -> None:
        self.voice_matches.append(
            (
                self.last_speech_t,
                get(ev, "utt_id"),
                get(ev, "person_id"),
                float(get(ev, "score", 0.0)),
            )
        )

    def on_transcript(self, ev, now: float) -> None:
        utt = str(get(ev, "utt_id"))
        prev = self.pending.get(utt)
        self.pending[utt] = _Pending(ev, prev.first_seen if prev else now)

    def on_appearance(self, ev) -> None:
        self.colors[int(get(ev, "track_id"))] = get(ev, "color")

    def on_description(self, ev) -> None:
        self.descriptions[int(get(ev, "track_id"))] = get(ev, "label")

    def on_name_proposal(self, ev) -> None:
        tid = get(ev, "track_id")
        if tid is None:
            return
        if get(ev, "state") == "proposed":
            self.proposals[int(tid)] = get(ev, "name")
        else:
            self.proposals.pop(int(tid), None)

    def forget_session(self) -> None:
        self.colors.clear()
        self.descriptions.clear()
        self.proposals.clear()
        self.exits.clear()
        self.voice_labels.clear()
        self.voice_matches.clear()
        self.utts.clear()
        self.harvester.reset()

    # ---------------- labels ----------------
    def label_for(self, info: _TrackInfo) -> str:
        if info.status in ("named", "enrolled") and info.name:
            return info.name
        if info.track_id in self.proposals:
            return f"{self.proposals[info.track_id]}?"
        if info.track_id in self.descriptions:
            return self.descriptions[info.track_id]
        if info.track_id in self.colors:
            return f"Person in {self.colors[info.track_id]}"
        return "Person"

    def _labels(self) -> dict[int, str]:
        """Labels for visible faces; repeated stranger labels get ", 2", ", 3"."""
        labels, counts = {}, {}
        for tid in sorted(self.tracks):
            info = self.tracks[tid]
            label = self.label_for(info)
            if info.status not in ("named", "enrolled") and label != "Person":
                counts[label] = counts.get(label, 0) + 1
                if counts[label] > 1:
                    label = f"{label}, {counts[label]}"
            labels[tid] = label
        return labels

    # ---------------- decision ----------------
    def _sensor_side(self, now: float) -> str:
        if self.levels is None or now - self.levels[0] > 0.5:
            return "none"
        diff = self.levels[1] - self.levels[2]
        if diff >= self.s.side_db:
            return "left"
        if diff <= -self.s.side_db:
            return "right"
        return "none"

    def _speaking(self, now: float) -> bool:
        return self.is_speech or now - self.last_speech_t <= self.s.speech_hangover_s

    def decide(self, now: float) -> tuple[Speaker | None, float | None]:
        """The speaker right now, and the in-time score behind it (face cases only)."""
        s = self.s
        if not self._speaking(now):
            return None, None

        if s.you_level_db is not None and self.levels and now - self.levels[0] <= 0.3:
            _, left, right = self.levels
            if min(left, right) >= s.you_level_db and abs(left - right) <= s.you_balance_db:
                return Speaker("you", label="You"), None

        labels = self._labels()
        fresh = [tr for tr in self.tracks.values() if now - tr.t <= 0.5]
        candidates = []
        for tr in fresh:
            if tr.lip_score >= s.lip_talking:
                r = in_time_score(tr.mouth, self.envelope, now, s.av_offset_s)
                if r is None or r >= s.sync_min_corr:
                    candidates.append((tr.lip_score, tr, r))
        cur = self.current
        if candidates:
            score, best, r = max(candidates, key=lambda c: c[0])
            if cur is not None and cur.kind == "face":
                held = next((c for c in candidates if c[1].track_id == cur.track_id), None)
                if held is not None and (
                    now - self._current_since < s.hold_s or score < s.switch_ratio * held[0]
                ):
                    score, best, r = held
            return Speaker("face", best.track_id, best.person_id, labels[best.track_id]), r
        if (
            cur is not None
            and cur.kind == "face"
            and now - self._current_since < s.hold_s
            and any(tr.track_id == cur.track_id for tr in fresh)
        ):
            tr = self.tracks[cur.track_id]
            return Speaker(
                "face", tr.track_id, tr.person_id, labels[tr.track_id]
            ), self._current_in_time

        band = [tr for tr in fresh if s.lip_uncertain <= tr.lip_score < s.lip_talking]
        still = all(tr.lip_score < s.lip_uncertain for tr in fresh if tr not in band)
        if len(band) == 1 and still:
            tr = band[0]
            return Speaker("probable_face", tr.track_id, tr.person_id, labels[tr.track_id]), None

        side = self._sensor_side(now)
        start = self.speech_start if self.speech_start is not None else now
        if now - start >= s.offscreen_after_s:
            matches = [
                m
                for m in self.voice_matches
                if m[0] >= start - 0.5 and m[2] and m[3] >= s.voice_match
            ]
            if matches:
                _, _, vid, _ = matches[-1]
                exit_side, exit_t = self.exits.get(vid, ("none", -1e9))
                if now - exit_t <= s.offscreen_exit_memory_s and exit_side != "none":
                    side = exit_side
                label = self.voice_labels.get(vid, "Someone")
                pid = None if vid.startswith("track-") else vid
                return Speaker("offscreen", None, pid, label, side), None
        return Speaker("someone", label="Someone", side=side), None

    # ---------------- captions ----------------
    def speaker_at(self, t: float) -> Speaker | None:
        """The speaker decided around time t, looking up to 0.5 s ahead for a late decision."""
        before, after = None, None
        for when, spk in self.timeline:
            if when <= t:
                before = spk
            elif when <= t + 0.5 and spk is not None and spk.kind != "someone":
                after = spk
                break
        if before is not None and before.kind != "someone":
            return before
        return after or before

    def _captions_for(self, ev, now: float, first_seen: float) -> list[Caption] | None:
        """The captions for one transcript draft, or None to wait a little for a speaker.

        The first draft shown is split where the speaker changes (then smoothed). Later
        drafts of the same utterance keep the segments already shown: each keeps its words
        (by time) and its speaker unless the evidence over it changes decisively, and only
        the new words at the end are split again. Segment ids that are no longer part of
        the utterance are queued in `retractions`.
        """
        words = [tuple(w) for w in (get(ev, "words") or [])]
        t_start = float(get(ev, "t_start", now))
        t_end = float(get(ev, "t_end", now))
        if not words:
            words = [(str(get(ev, "text", "")), t_start, t_end)]
        utt = str(get(ev, "utt_id"))
        mem = self.utts.get(utt)
        first = self.speaker_at(_mid(words[0]))
        if (
            mem is None
            and (first is None or first.kind == "someone")
            and now - first_seen < self.s.first_words_wait_ms / 1000
        ):
            return None  # wait a little for a speaker (only before anything is shown)
        evidence = [self.speaker_at(_mid(w)) or _someone() for w in words]
        if mem is None:
            mem = _UttMemory()
            groups = self._smooth(_raw_groups(list(zip(words, evidence))))
        else:
            groups = self._smooth(self._redraft(mem.segs, words, evidence))

        ids: list[str] = []
        for i, g in enumerate(groups):
            if i == 0:
                uid = utt  # the utterance's own id always holds its first segment
            else:
                uid = next((p for p in g.prev if p != utt and p not in ids), None)
                if uid is None:
                    uid = f"{utt}.{mem.next_index}"
                    mem.next_index += 1
            ids.append(uid)
        for gone in sorted(mem.sent - set(ids)):
            self.retractions.append(CaptionRetract(gone))
        mem.sent = set(ids)
        mem.segs = [
            _Seg(uid, g.speaker, float(g.words[0][1]), _end(g.words[-1], self.s.max_word_s))
            for uid, g in zip(ids, groups)
        ]
        mem.t = now
        final = bool(get(ev, "final", False))
        if final:
            self.utts.pop(utt, None)
        else:
            self.utts[utt] = mem
        lang = get(ev, "lang")
        if len(groups) == 1:
            return [Caption(utt, groups[0].speaker, str(get(ev, "text", "")), final, lang, words)]
        return [
            Caption(uid, g.speaker, " ".join(w[0] for w in g.words).strip(), final, lang, g.words)
            for uid, g in zip(ids, groups)
        ]

    def _redraft(self, segs: list[_Seg], words: list, evidence: list[Speaker]) -> list[_Group]:
        """Fit a new draft onto the segments already shown.

        Words fall into the old segments by time. Each old segment keeps its speaker
        unless another known speaker now covers `relabel_share` of its speech
        (`claim_share` if it showed "Someone"); "Someone" never replaces a known
        speaker, so a face leaving the frame re-labels nothing said before. Words after
        the old end are split afresh, together with the last segment's trailing words
        that only joined it while too short to stand alone: once such a run is long
        enough it becomes its own segment (a real turn change, or words said after the
        speaker left the frame), otherwise it simply joins the last segment again.
        """
        bounds = [(a.t_hi + b.t_lo) / 2 for a, b in pairwise(segs)]
        per_seg: list[list] = [[] for _ in segs]
        tail: list = []
        for w, spk in zip(words, evidence):
            m = _mid(w)
            if m > segs[-1].t_hi:
                tail.append((w, spk))
            else:
                per_seg[bisect_right(bounds, m)].append((w, spk))
        # the last segment's trailing words that only joined it while too short to stand
        # alone (their evidence points elsewhere) are split again with the new words
        last, mine = per_seg[-1], _who(segs[-1].speaker)
        k = len(last)
        while k > 1 and _who(last[k - 1][1]) != mine:
            k -= 1
        reopened, per_seg[-1] = last[k:], last[:k]
        locked = [
            _Group(
                self._keep_or_relabel(seg.speaker, pairs),
                [w for w, _ in pairs],
                [seg.uid],
                True,
                [spk for _, spk in pairs],
            )
            for seg, pairs in zip(segs, per_seg)
            if pairs  # a segment whose words all vanished from the draft is dropped
        ]
        fresh = _raw_groups(reopened + tail)
        n = 0
        for g in fresh:
            g.shown = n < len(reopened)
            n += len(g.words)
        return locked + fresh

    def _keep_or_relabel(self, cur: Speaker, pairs: list) -> Speaker:
        """A shown segment's speaker, given the evidence now over its words."""
        share: dict[tuple, float] = {}
        best_of: dict[tuple, Speaker] = {}
        for w, spk in pairs:
            key = _who(spk)
            share[key] = share.get(key, 0.0) + _dur(w, self.s.max_word_s)
            best_of[key] = _better(best_of.get(key), spk)
        total = sum(share.values()) or 1.0
        mine = _who(cur)
        others = [k for k in share if k != mine and k[0] != "someone"]
        if others:
            top = max(others, key=lambda k: share[k])
            need = self.s.claim_share if cur.kind == "someone" else self.s.relabel_share
            if share[top] / total >= need:
                return best_of[top]
        if mine in best_of:
            return _refresh(cur, best_of[mine])
        return cur

    def _smooth(self, groups: list[_Group]) -> list[_Group]:
        """Stop a flickering speaker decision from chopping a sentence into pieces.

        A short "Someone" piece (under 2x `min_segment_s`) joins the known
        speaker next to it, and any other piece under `min_segment_s` joins its
        longer neighbour. Real turn changes (each side longer) stay split. A piece
        already shown with a known speaker (locked) and at least `min_segment_s`
        long never takes another's speaker.
        """

        def span(ws: list) -> float:
            return _end(ws[-1], self.s.max_word_s) - float(ws[0][1])

        short = self.s.min_segment_s

        def fold(gs: list[_Group], i: int, j: int) -> None:
            # piece i joins its neighbour j and takes j's speaker
            lo, hi = min(i, j), max(i, j)
            gs[lo : hi + 1] = [
                _Group(
                    gs[j].speaker,
                    gs[lo].words + gs[hi].words,
                    gs[lo].prev + gs[hi].prev,
                    gs[lo].locked or gs[hi].locked,
                    gs[lo].evidence + gs[hi].evidence,
                    gs[lo].shown or gs[hi].shown,
                )
            ]

        def rule(gs: list[_Group], i: int) -> tuple[int, int | None]:
            """(priority, neighbour to join) for piece i; lower priority acts first."""
            g = gs[i]
            if g.locked and g.speaker.kind != "someone":
                return 9, None  # shown with a known speaker: it keeps it
            nbrs = [k for k in (i - 1, i + 1) if 0 <= k < len(gs)]
            known = [k for k in nbrs if gs[k].speaker.kind != "someone"]
            sandwiched = len(nbrs) == 2 and _who(gs[i - 1].speaker) == _who(gs[i + 1].speaker)
            if sandwiched and span(g.words) < short:
                return 0, i - 1
            if g.speaker.kind == "someone" and known and span(g.words) < 2 * short:
                return 1, max(known, key=lambda k: span(gs[k].words))
            if g.shown and known and span(g.words) < self.s.move_segment_s:
                # words would leave the bubble they were shown in: that needs more evidence
                return 1, max(known, key=lambda k: span(gs[k].words))
            if span(g.words) < short:
                return 2, max(nbrs, key=lambda k: span(gs[k].words))
            return 9, None

        gs = _merge_neighbours(groups)
        while len(gs) > 1:
            # sandwiched flickers, then unknown words, then the shortest other piece
            best = min(range(len(gs)), key=lambda k: (rule(gs, k)[0], span(gs[k].words)))
            _, j = rule(gs, best)
            if j is None:
                break
            fold(gs, best, j)
            gs = _merge_neighbours(gs)  # neighbours that now share a speaker become one piece
        return gs

    def take_retractions(self) -> list[CaptionRetract]:
        """Segment ids to retract since the last call (the service publishes them)."""
        out, self.retractions = self.retractions, []
        return out

    # ---------------- tick ----------------
    def tick(self, now: float) -> tuple[Scene, list[Caption], VoiceHarvest | None]:
        spk, r = self.asd_gate.decide(self, now)
        if not _same(spk, self.current):
            self._current_since = now
        self.current = spk
        self._current_in_time = r
        if not self.timeline or not _same(self.timeline[-1][1], spk):
            self.timeline.append((now, spk))
        while self.timeline and now - self.timeline[0][0] > 30:
            self.timeline.popleft()

        captions = []
        for utt in list(self.pending):
            p = self.pending[utt]
            out = self._captions_for(p.event, now, p.first_seen)
            if out is not None:
                captions += out
                del self.pending[utt]
        for utt in [u for u, m in self.utts.items() if now - m.t > self.s.utterance_memory_s]:
            del self.utts[utt]  # its final never came (audio restarted): stop tracking it

        confident = None
        if spk is not None and spk.kind == "face" and r is not None and r >= self.s.sync_min_corr:
            confident = voice_id(spk.person_id, spk.track_id)
        harvest = self.harvester.update(now, confident)
        return self.scene(), captions, harvest

    def scene(self) -> Scene:
        labels = self._labels()
        cur = self.current
        faces = []
        for tid in sorted(self.tracks):
            info = self.tracks[tid]
            speaking = (
                cur is not None and cur.kind in ("face", "probable_face") and cur.track_id == tid
            )
            status = (
                "proposed" if tid in self.proposals and info.status == "unknown" else info.status
            )
            faces.append(
                FaceState(
                    tid,
                    info.box,
                    labels[tid],
                    status,
                    info.lip_score,
                    speaking,
                    speaking and cur.kind == "probable_face",
                )
            )
        offscreen = []
        if cur is not None and cur.kind in ("offscreen", "someone"):
            offscreen.append(Offscreen(cur.person_id, cur.label, cur.side))
        return Scene(
            self.frame_no, self.frame_t, faces, offscreen, cur is not None and cur.kind == "you"
        )
