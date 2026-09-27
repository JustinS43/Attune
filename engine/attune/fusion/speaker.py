"""Decides who is talking, gives captions their speaker, and builds the scene.

Section 1 - Vision (fusion). TODO: V-09. Plan: section 05 "Who's talking".

While speech is detected, each tick (15 per second) checks, in order:
1. You: both glasses sensors are above the calibrated own-voice level and
   within 3 dB of each other.
2. A visible speaker: a face that is talking (see below), or whose voice print
   matches this utterance while its lips move at least in the probable band.
   Held at least 0.5 s; only switches to someone scoring 1.5x higher. Continuity:
   while speech runs on without a pause and no other face talks, the face keeps
   the speech for up to `continuity_s` after its last own evidence (only
   `asd_continuity_s` when Light-ASD scores it silent: a hand by the mouth), unless
   its voice is vetoed (`_continues`).
3. A probable visible speaker: exactly one face that passes the talking checks
   only in the probable band (`lip_uncertain`), while everyone else is still.
   Dashed tail.
4. Off-screen: after ~1 s of speech, a voice match >= 0.5 names the speaker;
   the side comes from where they left the frame (< 30 s ago), otherwise
   from the louder sound sensor (>= 3 dB). Until then: "Someone". A voice
   whose face is in view is never off screen: it goes to that face, dashed,
   or to "Someone" when Light-ASD calls that face silent (a match that still
   hears an earlier turn, `_stale_voice`, which also never vetoes the face
   Light-ASD hears talking now).

Talking (V-19). A live face is never perfectly still: landmark jitter, lips
parting, a yawn, a head turn. So, while speech is heard, a face is talking
only when all of these hold:
- its lip score (band-passed, vision/mouth.py) is at least in the probable band:
  >= `lip_uncertain`, and >= its own noise floor (the `lip_floor_pct`
  percentile of its lip score over the last `lip_floor_s`) x `lip_floor_ratio`
  x lip_uncertain/lip_talking; its mouth isn't wide open (`lip_open_max`: a
  yawn or a laugh) and its head isn't moving fast (`head_motion_max`: motion
  blur makes the lip landmarks jump);
- with sound available, its lips are in time with it (`sync_min_corr`):
  shown, not merely "can't tell" (`require_sync` off skips this, for film reels
  played over unrelated audio);
- its mouth is sampled steadily (>= `mouth_min_fps` samples a second): at a
  few frames a second (a starved GPU) the ratio jumps between far-apart
  moments, so the mouth counts as not measured rather than as moving;
- both have held for `talk_cover_share` of at least `talk_confirm_s` of speech.
  A face joining in the middle of someone else's speech must also open and
  close repeatedly (`talk_min_swings`) within `talk_sustain_s`: lips parting
  once are only two swings;
- it moved clearly (>= `lip_talking`, and its floor x `lip_floor_ratio`) at
  some point in that time; if not, it is only a probable speaker.
The in-time check alone is weak evidence: on a live clip a silent listener's
mouth reached r >= 0.3 about as often against the room's speech shifted by
seconds as against the real one, so it is one requirement among several.

Voices. This utterance's `audio.voice_match` vetoes a face when it matches
someone else: a saved contact, a stranger ("track-N") or automatic contact
("auto-...") who was on screen on another face at the same time as this face, or an
off-screen voice ("offscreen-N") while Light-ASD doesn't hear this face talking
(V-31, `_voice_verdict`). No match is inconclusive. A match to the face's own print
(or its track's print from before it was recognised) lets it speak with the
probable-band mouth bar.
Voice prints are harvested from a face that is talking on its own evidence
with r >= `harvest_min_corr`, and as "offscreen-N" (`learn_offscreen`) from
speech heard while every visible face's mouth is measured and still; a later
match to one reads "Someone". A face that talks on its own evidence with an
off-screen voice for `offscreen_claim_s` claims it (its mouth was covered, say,
while that voice was learnt).

Light-ASD (V-22, asd_gate.py). For a face with a fresh `asd_score`, Light-ASD's verdict
(lips and sound together) replaces the talking checks above in `decide`; the voice
veto, the hold and switch rules and "You" still apply. Other faces keep the checks.

Captions: every word has a time, so a transcript is split where the speaker
changes (a switch Light-ASD made is dated back `asd_switch_lag_s`, and each change
moves back to the pause before it, `_snap`). If nobody qualifies yet, the first words wait up to 300 ms for a
speaker before showing as "Someone". Once a segment is shown, later drafts keep
its words and speaker unless the evidence over most of it changes (see
`_redraft`); a face leaving never re-labels what it already said, and segment
ids that drop out are retracted (`caption.retract`).

Cloud captions (V-32, cloud_tags.py). While the wearer has them on, Google's per-word
speaker tags are one more input: each caption word's evidence comes from the tag's
face, a new voice gets its own bubble at once, and a final waits a moment for its tags.
Without them (`cloud=None`, or no tags) nothing here changes.

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
from .cloud_tags import CloudTags
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
    first_t: float = 0.0  # when the face was first seen
    mouth: deque = field(default_factory=lambda: deque(maxlen=150))
    lips: deque = field(default_factory=deque)  # (t, lip_score) for the noise floor
    moves: deque = field(default_factory=lambda: deque(maxlen=30))  # (t, cx, cy, width)
    ticks: deque = field(default_factory=deque)  # (t, speaking, passing, moving) per tick
    r: float | None = None  # in-time score this tick
    moving: bool = False  # lip score >= this face's talking line (and not a yawn)
    stirring: bool = False  # lip score >= this face's probable line (and not a yawn)
    measured: bool = False  # its mouth is sampled steadily enough to judge (mouth_min_fps)
    measured_since: float = 0.0  # when it last became so
    claims: deque = field(default_factory=deque)  # (t, off-screen voice it talked with)
    talking: bool = False  # passes every talking check this tick
    probable: bool = False  # passes them in the probable band


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
    def __init__(self, settings: FusionSettings | None = None, cloud: CloudTags | None = None):
        self.s = settings or FusionSettings()
        self.cloud = cloud  # V-32: cloud captions' speaker tags (None: not used)
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
        self.harvested: set[str] = set()  # voice ids given a session print (voice.harvest)
        self._decided_by: str | None = None  # why decide() chose a face; gates harvesting
        self._evidence: tuple[int | None, float] = (None, -1e9)  # last face chosen on evidence
        self._earned: tuple[float | None, dict[int, float]] = (None, {})  # speech run -> s talked
        self._stopped: dict[int, float] = {}  # track -> when it last stopped talking (5 s)
        self.last_seen: dict[int, float] = {}  # track -> when it was last on screen
        # voice id -> {track: when last on screen with it}: whether a saved or automatic
        # contact was in view on another face at the same time as a given face (V-31)
        self.voice_seen: dict[str, dict[int, float]] = {}
        self._offscreen_n = 0  # "offscreen-N" voices learnt this session
        self._offscreen_run: tuple[float, str] | None = None  # (speech start, its new id)
        self.claimed: dict[str, str] = {}  # "offscreen-N" -> the voice id of the face it was

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
                info = self.tracks[tid] = _TrackInfo(tid, [], 0.0, None, None, "unknown", t, t)
            info.box = list(get(tr, "box"))
            info.lip_score = float(get(tr, "lip_score", 0.0))
            info.person_id = get(tr, "person_id")
            info.name = get(tr, "name")
            info.status = get(tr, "status", "unknown")
            info.t = t
            self.last_seen[tid] = t
            info.lips.append((t, info.lip_score))
            x, y, w, h = (float(v) for v in info.box[:4])
            info.moves.append((t, x + w / 2, y + h / 2, max(w, 1.0)))
            while info.lips and t - info.lips[0][0] > self.s.lip_floor_s:
                info.lips.popleft()
            mouth = get(tr, "mouth_open")
            if mouth is not None:
                info.mouth.append((t, float(mouth)))
            vid = voice_id(info.person_id, tid)
            self.voice_labels[vid] = self.label_for(info)
            self.voice_seen.setdefault(vid, {})[tid] = t
        for tid in [k for k in self.tracks if k not in seen]:
            del self.tracks[tid]

    def on_track_lost(self, ev) -> None:
        tid = int(get(ev, "track_id"))
        side = get(ev, "side", "none")
        t = float(get(ev, "t"))
        info = self.tracks.pop(tid, None)
        if info is not None and info.talking:
            self._stopped[tid] = t
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

    def on_cloud_words(self, ev, now: float) -> None:
        """V-32: `speaker.cloud`, per-word speaker tags from cloud captions."""
        if self.cloud is not None:
            self.cloud.on_words(ev, now)

    def on_cloud_state(self, ev) -> None:
        """V-32: `cloud.state` (a final waits for tags only while it is "on")."""
        if self.cloud is not None:
            self.cloud.on_state(ev)

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
        self.harvested.clear()
        self.last_seen.clear()
        self.voice_seen.clear()
        self._offscreen_n = 0
        self._offscreen_run = None
        self.claimed.clear()
        if self.cloud is not None:
            self.cloud.forget()

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

    def _floor(self, tr: _TrackInfo, now: float) -> float:
        """This face's resting lip score: a low percentile of its recent scores (0 until known)."""
        if not tr.lips or now - tr.lips[0][0] < self.s.lip_floor_min_s:
            return 0.0
        vals = [v for t, v in tr.lips if now - t <= self.s.lip_floor_s]
        return float(np.percentile(vals, self.s.lip_floor_pct)) if vals else 0.0

    @staticmethod
    def _head_speed(tr: _TrackInfo, now: float) -> float:
        """How fast the face moves (face widths per second, median over the last 0.5 s)."""
        pts = [m for m in tr.moves if now - m[0] <= 0.5]
        speeds = [
            math.hypot(b[1] - a[1], b[2] - a[2]) / b[3] / (b[0] - a[0])
            for a, b in pairwise(pts)
            if b[0] > a[0]
        ]
        return float(np.median(speeds)) if speeds else 0.0

    def _measured(self, tr: _TrackInfo, now: float) -> bool:
        """Whether this face's mouth is sampled steadily enough (mouth_min_fps) to judge.

        At a few frames per second (a starved GPU) or with landmarks failing, the mouth
        ratio jumps between far-apart moments and reads like lips moving.
        """
        window = min(1.0, now - tr.first_t)
        if window < 0.5:
            return False
        pts = [t for t, _ in tr.mouth if now - t <= window]
        return bool(pts) and len(pts) >= self.s.mouth_min_fps * window and now - pts[-1] <= 0.3

    @staticmethod
    def _swings(tr: _TrackInfo, now: float, span_s: float, amp: float) -> int:
        """Open/close swings of at least `amp` (open ratio) in the last `span_s`."""
        raw = [m for t, m in tr.mouth if now - t <= span_s]
        if len(raw) < 3:
            return 0
        v = [float(np.median(raw[max(i - 1, 0) : i + 2])) for i in range(len(raw))]
        swings, rising, peak, trough = 0, None, v[0], v[0]
        for x in v[1:]:
            if rising is not True and x >= trough + amp:  # opened from the lowest point
                swings, rising, peak = swings + 1, True, x
            elif rising is not False and x <= peak - amp:  # closed from the highest point
                swings, rising, trough = swings + 1, False, x
            elif rising is True:
                peak = max(peak, x)
            elif rising is False:
                trough = min(trough, x)
            else:
                peak, trough = max(peak, x), min(trough, x)
        return swings

    def _sound_known(self, now: float) -> bool:
        """Whether there's a loudness envelope to check lips against."""
        return bool(self.envelope.samples) and now - self.envelope.samples[-1][0] <= 1.0

    def _assess(self, now: float) -> None:
        """Update every face's talking checks for this tick (see the module docstring)."""
        s = self.s
        speaking = self._speaking(now)
        sound = self._sound_known(now)
        run_start = self.speech_start if self.speech_start is not None else now
        keep = max(s.talk_sustain_s, s.talk_confirm_s) + 1.0
        for tr in self.tracks.values():
            was_talking = tr.talking
            self._assess_face(tr, now, speaking, sound, run_start, keep)
            if was_talking and not tr.talking:
                self._stopped[tr.track_id] = now
        for tid in [k for k, t in self._stopped.items() if now - t > 5.0]:
            del self._stopped[tid]

    def _assess_face(
        self, tr: _TrackInfo, now: float, speaking: bool, sound: bool, run_start: float, keep: float
    ) -> None:
        """One face's talking checks for this tick."""
        s = self.s
        measured = now - tr.t <= 0.5 and self._measured(tr, now)
        if measured and not tr.measured:
            tr.measured_since = now
        tr.measured = measured
        if not tr.measured:
            # gone, or its mouth isn't sampled steadily: no mouth evidence either way
            tr.talking = tr.probable = tr.moving = tr.stirring = False
            tr.r = None
            tr.ticks.clear()
            return
        need = max(s.lip_talking, s.lip_floor_ratio * self._floor(tr, now))
        need_low = need * s.lip_uncertain / s.lip_talking
        wide = any(v >= s.lip_open_max for t, v in tr.mouth if now - t <= 1.0)
        unsure = wide or self._head_speed(tr, now) > s.head_motion_max
        tr.moving = tr.lip_score >= need and not unsure
        tr.stirring = tr.lip_score >= need_low and not unsure
        tr.r = (
            in_time_score(tr.mouth, self.envelope, now, s.av_offset_s, s.av_window_s)
            if tr.stirring
            else None
        )
        # With sound, being in time has to be shown. With none at all it can't be checked,
        # and a reel played over unrelated audio (require_sync off) doesn't check it.
        if sound and s.require_sync:
            in_time = tr.r is not None and tr.r >= s.sync_min_corr
        else:
            in_time = True
        passing = tr.stirring and in_time
        # moving but seen too briefly to check against the sound yet: not held against it
        unchecked = tr.stirring and sound and s.require_sync and tr.r is None
        tr.ticks.append((now, speaking, passing or unchecked, tr.moving))
        while tr.ticks and now - tr.ticks[0][0] > keep:
            tr.ticks.popleft()
        since = self._streak_start(tr) if speaking and passing else None
        if since is None:
            tr.talking = tr.probable = False
            return
        # A face that started moving in time as the speech started (or as it came into view,
        # or as a visible talker, itself included, stopped) needs talk_confirm_s of it; one
        # that started in the middle of someone else's speech needs repeated mouth
        # swings. Waiting the full swing-history window after those swings are already
        # present delays a real turn without adding evidence.
        turn = max(self._stopped.values(), default=-1e9)
        at_onset = since <= max(run_start, tr.measured_since, turn) + s.talk_onset_s
        steady = now - since >= s.talk_confirm_s - 1e-6
        if steady and not at_onset:
            # joining someone's speech: talking opens and closes the mouth again and again,
            # a listener's lips parting and closing once is two swings
            # Count swings only since this run began; a prior yawn or lip movement
            # cannot qualify a new, otherwise brief coincidence with the sound.
            span = min(s.talk_sustain_s, now - since)
            steady = self._swings(tr, now, span, s.talk_swing_amp) >= s.talk_min_swings
        clear = any(x[3] for x in tr.ticks if x[0] >= since)
        tr.talking = steady and clear
        tr.probable = steady and not clear

    def _streak_start(self, tr: _TrackInfo) -> float:
        """When this face's current run of passing started, over heard ticks.

        The earliest passing tick from which at least `talk_cover_share` of the heard ticks
        up to now passed, so a gap of a tick or two doesn't restart it.
        """
        heard = [x for x in tr.ticks if x[1]]
        since, passed = heard[-1][0], 0
        for n, x in enumerate(reversed(heard), 1):
            passed += x[2]
            if x[2] and passed >= self.s.talk_cover_share * n:
                since = x[0]
            elif passed < self.s.talk_cover_share * n - 3:
                break  # can't recover further back
        return since

    def _latest_voice_match(self, now: float) -> tuple[float, str, str | None, float] | None:
        """Latest voice result for the current speech run, including a non-match."""
        start = self.speech_start if self.speech_start is not None else now
        return next((m for m in reversed(self.voice_matches) if m[0] >= start - 0.5), None)

    def _voice_verdict(self, tr: _TrackInfo, now: float) -> str | None:
        """What this utterance's voice match says about a face: "veto", "support" or None.

        V-31: a stranger's or an automatic contact's voice vetoes a face only when its owner
        was in view on another face at the same time as this face: an automatic print may
        be this very face (not recognised yet on a new track, or saved twice), as a
        stranger's may be the same person tracked again. The face's own print from before
        it was recognised ("track-N") supports it. An off-screen voice ("offscreen-N")
        never vetoes the face Light-ASD hears talking: it may have been learnt from this
        face while a hand hid its mouth (that face then claims it, `_claim`). A saved
        contact's voice still vetoes any other face.
        """
        match = self._latest_voice_match(now)
        if match is None:
            return None
        _, _, pid, score = match
        if score < self.s.voice_match or pid is None:
            return None  # nobody matched: inconclusive, it must not overrule lips and sound
        if self._own_voice(pid, tr):
            return "support"
        if self._stale_voice(pid, now):
            return None
        if self._asd_hears(tr, now) and self._quiet_in_view(pid, now):
            return None  # Light-ASD hears this face; the voice's face in view is still: stale
        if self._seen_elsewhere(pid, tr):
            return "veto"  # its owner is (or was, beside this face) another face in view
        if pid.startswith(("track-", "auto-")):
            # a stranger or an automatic contact never in view beside this face: it may be
            # this same person (tracked again, not recognised yet, or saved twice)
            return None
        if pid.startswith("offscreen-") and self._asd_hears(tr, now):
            return None  # an off-screen voice vs Light-ASD's lips and sound: they win
        return "veto"

    def _own_voice(self, pid: str, tr: _TrackInfo) -> bool:
        """Is voice id `pid` this face's own: its print, its track's print from before it was
        recognised, or an off-screen voice it claimed?"""
        mine = (voice_id(tr.person_id, tr.track_id), f"track-{tr.track_id}")
        return pid in mine or self.claimed.get(pid) in mine

    def _seen_elsewhere(self, pid: str, tr: _TrackInfo) -> bool:
        """Was voice `pid`'s owner in view on another face since this face appeared?"""
        if pid.startswith("track-"):
            other = pid.removeprefix("track-")
            seen = self.last_seen.get(int(other)) if other.isdigit() else None
            return int(other) != tr.track_id and seen is not None and seen >= tr.first_t
        owner = self.claimed.get(pid, pid)
        return any(
            tid != tr.track_id and t >= tr.first_t
            for tid, t in self.voice_seen.get(owner, {}).items()
        )

    def _asd_hears(self, tr: _TrackInfo, now: float) -> bool:
        """Does Light-ASD (a fresh score, in its talking state) hear this face talking?"""
        asd = self.asd_gate.covered(self, now).get(tr.track_id)
        return asd is not None and asd.talking

    def _visible_track(self, vid: str, now: float) -> _TrackInfo | None:
        """The face in view right now that voice id `vid` belongs to, if any."""
        vid = self.claimed.get(vid, vid)
        for tr in self.tracks.values():
            if now - tr.t <= 0.5 and vid in (
                voice_id(tr.person_id, tr.track_id),
                f"track-{tr.track_id}",  # its print from before it was recognised
            ):
                return tr
        return None

    def _stale_voice(self, vid: str, now: float) -> bool:
        """Does this voice match belong to a face in view that Light-ASD says is silent now?

        The match is made over the newest `voice_match_max_s` of speech, so just after a
        turn it still hears mostly the previous talker. When that talker is on screen and
        Light-ASD (lips and sound together, the last 0.4 s) calls them silent, the match
        is about earlier speech: it must not veto the face that is talking now.
        """
        tr = self._visible_track(vid, now)
        if tr is None:
            return False
        asd = self.asd_gate.covered(self, now).get(tr.track_id)
        return asd is not None and asd.score < self.s.asd_off

    def _quiet_in_view(self, vid: str, now: float) -> bool:
        """Is voice `vid`'s face in view, not scored by Light-ASD, and its mouth measured and
        still? (The voice match then hears an earlier turn, like `_stale_voice`.)"""
        tr = self._visible_track(vid, now)
        return (
            tr is not None
            and tr.track_id not in self.asd_gate.covered(self, now)
            and tr.measured
            and not (tr.stirring or tr.talking)
        )

    def _claim(self, now: float) -> None:
        """Let a face that clearly talks with an off-screen voice claim it (offscreen_claim_s)."""
        s = self.s
        match = self._latest_voice_match(now)
        pid = match[2] if match is not None and match[3] >= s.voice_match else None
        if pid is None or not pid.startswith("offscreen-") or pid in self.claimed:
            pid = None
        for tr in self.tracks.values():
            strong = (
                tr.talking and tr.r is not None and tr.r >= s.harvest_min_corr
            ) or self._asd_hears(tr, now)
            tr.claims.append((now, pid if strong else None))
            while tr.claims and now - tr.claims[0][0] > s.offscreen_claim_s:
                tr.claims.popleft()
            if pid is None or now - tr.claims[0][0] < s.offscreen_claim_s - 0.2:
                continue
            share = sum(c == pid for _, c in tr.claims) / len(tr.claims)
            if share >= s.talk_cover_share:
                self.claimed[pid] = voice_id(tr.person_id, tr.track_id)

    def _continues(self, track_id: int, now: float) -> bool:
        """Is this face still talking in the same unbroken stretch of speech? (continuity)

        A talker's evidence dips mid-sentence: a hand or a cup over the mouth, a head turn,
        a Light-ASD window that lands on a breath. Nobody else started talking (no other
        face is a candidate, or `decide` would have taken it), speech has not paused since
        this face last talked on its own evidence (a pause is `speech_hangover_s`: a turn
        can start there), that was under `continuity_s` ago, the face has earned it (see
        below), and the voice has not vetoed it. When Light-ASD scores the face and calls it
        silent, only for `asd_continuity_s` (V-31; `continuity_s` while the voice match is
        this face's own): a hand resting by the mouth drops Light-ASD's score for a moment
        while the person talks on; a longer silence is its verdict (someone off camera took
        over without a pause).
        """
        s = self.s
        tid, t = self._evidence
        if s.continuity_s <= 0 or tid != track_id or now - t > s.continuity_s:
            return False
        run = self.speech_start if self.speech_start is not None else t
        if run > t:
            return False  # speech paused and started again since: maybe another voice
        # Trust is earned: a listener whose lips lined up with someone else's speech for a
        # moment must not keep that speech. The face talked on its own evidence for at least
        # continuity_min_s, and for continuity_share of this stretch of speech so far.
        earned = self._earned[1].get(track_id, 0.0) if self._earned[0] == run else 0.0
        if earned < s.continuity_min_s or earned < s.continuity_share * (now - run):
            return False
        if track_id not in self.asd_gate.covered(self, now):
            return True
        # its own voice still heard: the whole continuity_s, like a face Light-ASD can't score
        own = track_id in self.tracks and self._voice_verdict(self.tracks[track_id], now)
        return now - t <= (s.continuity_s if own == "support" else s.asd_continuity_s)

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
        verdict = {tr.track_id: self._voice_verdict(tr, now) for tr in fresh}
        candidates = []
        for tr in fresh:
            if verdict[tr.track_id] == "veto":
                continue
            if tr.talking:
                candidates.append((tr.lip_score, tr, tr.r, "talking"))
            elif verdict[tr.track_id] == "support" and tr.stirring and (tr.r is None or tr.r >= 0):
                # its own voice: lips moving in the probable band and not against the sound
                candidates.append((tr.lip_score, tr, tr.r, "voice"))
        cur = self.current
        if candidates:
            score, best, r, why = max(candidates, key=lambda c: c[0])
            if cur is not None and cur.kind == "face":
                held = next((c for c in candidates if c[1].track_id == cur.track_id), None)
                if held is not None and (
                    now - self._current_since < s.hold_s or score < s.switch_ratio * held[0]
                ):
                    score, best, r, why = held
            self._decided_by = why
            self._evidence = (best.track_id, now)
            run = self.speech_start if self.speech_start is not None else now
            if self._earned[0] != run:
                self._earned = (run, {})
            earned = self._earned[1]
            earned[best.track_id] = earned.get(best.track_id, 0.0) + 1.0 / s.rate_hz
            return Speaker("face", best.track_id, best.person_id, labels[best.track_id]), r
        if (
            cur is not None
            and cur.kind == "face"
            and (now - self._current_since < s.hold_s or self._continues(cur.track_id, now))
            and any(tr.track_id == cur.track_id and verdict[tr.track_id] != "veto" for tr in fresh)
        ):
            tr = self.tracks[cur.track_id]
            self._decided_by = "hold"
            return Speaker(
                "face", tr.track_id, tr.person_id, labels[tr.track_id]
            ), self._current_in_time

        band = [tr for tr in fresh if tr.probable and verdict[tr.track_id] != "veto"]
        still = all(not tr.stirring for tr in fresh if tr not in band)
        if len(band) == 1 and still:
            tr = band[0]
            self._decided_by = "probable"
            return Speaker("probable_face", tr.track_id, tr.person_id, labels[tr.track_id]), tr.r

        side = self._sensor_side(now)
        start = self.speech_start if self.speech_start is not None else now
        if now - start >= s.offscreen_after_s:
            match = self._latest_voice_match(now)
            if match is not None and match[2] and match[3] >= s.voice_match:
                _, _, vid, _ = match
                if vid in self.claimed:  # an off-screen voice that turned out to be a face
                    vid = self.claimed[vid]
                elif vid.startswith("offscreen-"):  # a voice only ever heard off screen
                    return Speaker("someone", label="Someone", side=side), None
                seen = self._visible_track(vid, now)
                if seen is not None:
                    # the voice of a face in view is never shown as off screen: that face,
                    # dashed (the voice says so, the lips don't), unless Light-ASD calls it
                    # silent (a match that still hears an earlier turn: "Someone")
                    if self._stale_voice(vid, now):
                        return Speaker("someone", label="Someone", side=side), None
                    self._decided_by = "voice-in-view"
                    return Speaker(
                        "probable_face", seen.track_id, seen.person_id, labels[seen.track_id]
                    ), seen.r
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

    def _speaker_may_come(self, now: float) -> bool:
        """Could a speaker be decided within `first_words_wait_ms`? (V-24)

        Holding the first words back only helps while a visible mouth is moving but not
        yet judged talking (or Light-ASD's score for it is not clearly "silent"), or while
        a known voice may still be matched. With every face still or none on screen, and
        no voice to match, the words are shown at once as "Someone".
        """
        covered = self.asd_gate.covered(self, now)
        for tr in self.tracks.values():
            if now - tr.t > 0.5:
                continue
            asd = covered.get(tr.track_id)
            if asd is not None:
                if asd.score >= self.s.asd_off:
                    return True
            elif tr.stirring or tr.moving or tr.probable:
                return True
        start = self.speech_start if self.speech_start is not None else now
        known = any(m[2] for m in self.voice_matches)
        heard = any(m[0] >= start - 0.5 for m in self.voice_matches)
        return known and not heard

    def _captions_for(self, ev, now: float, first_seen: float) -> list[Caption] | None:
        """The captions for one transcript draft, or None to wait a little for a speaker.

        The first draft shown is split where the speaker changes (then smoothed). Later
        drafts of the same utterance keep the segments already shown: each keeps its words
        (by time) and its speaker unless the evidence over it changes decisively, and only
        the new words at the end are split again. Segment ids that are no longer part of
        the utterance are queued in `retractions`.
        """
        if self.cloud is not None and self.cloud.wait(ev, now):
            return None  # V-32: a final waits a moment for the cloud's tags on its words
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
            and self._speaker_may_come(now)
        ):
            return None  # wait a little for a speaker (only before anything is shown)
        evidence = [self.speaker_at(_mid(w)) or _someone() for w in words]
        if self.cloud is not None:
            evidence = self.cloud.evidence(self, words, evidence, now)  # V-32
        # V-31: each change moves back to its pause before smoothing too, so a short reply
        # whose evidence came late is long enough to keep its own bubble
        if mem is None:
            mem = _UttMemory()
            groups = self._smooth(self._snap(_raw_groups(list(zip(words, evidence)))))
        else:
            groups = self._smooth(self._snap(self._redraft(mem.segs, words, evidence)))
        groups = self._snap(groups)

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
        long never takes another's speaker. V-31: neither does a known speaker's piece of at
        least `turn_min_s` next to another known speaker, with a pause (`snap_gap_s`) on both
        sides: a short reply ("Okay.", "Do you like it?") is a turn of its own, not a flicker
        inside someone's sentence.
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

        def turn(gs: list[_Group], i: int) -> bool:
            """Piece i is a short turn between two known talkers: another known speaker is
            next to it, with a pause (or the caption's edge) on both sides."""
            g, gap, cap = gs[i], self.s.snap_gap_s, self.s.max_word_s
            if g.speaker.kind == "someone" or span(g.words) < self.s.turn_min_s:
                return False
            nbrs = [gs[k] for k in (i - 1, i + 1) if 0 <= k < len(gs)]
            if not any(
                n.speaker.kind != "someone" and _who(n.speaker) != _who(g.speaker) for n in nbrs
            ):
                return False
            ws = g.words
            before = i == 0 or float(ws[0][1]) - _end(gs[i - 1].words[-1], cap) >= gap
            after = i == len(gs) - 1 or float(gs[i + 1].words[0][1]) - _end(ws[-1], cap) >= gap
            return before and after

        def rule(gs: list[_Group], i: int) -> tuple[int, int | None]:
            """(priority, neighbour to join) for piece i; lower priority acts first."""
            g = gs[i]
            if g.locked and g.speaker.kind != "someone":
                return 9, None  # shown with a known speaker: it keeps it
            if turn(gs, i):
                return 9, None  # a short reply between pauses: a turn of its own
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

        if self.cloud is not None:
            groups = self.cloud.protect(groups)  # V-32: a turn the cloud heard stays split
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

    def _snap(self, groups: list[_Group]) -> list[_Group]:
        """Move each speaker change back to the pause it really happened at.

        The evidence for a new talker arrives 0.5-1 s after they start (Light-ASD's window,
        the hold), so the first words of a reply ("Yeah, well, you know") would end the
        previous speaker's bubble. People leave a small pause before they answer: within
        `snap_max_s` before a change to a known speaker, the longest gap between words
        (at least `snap_gap_s`, and longer than the gap at the change itself) becomes
        the change. The earlier piece always keeps at least one word.
        """
        s = self.s
        if s.snap_max_s <= 0:
            return groups
        for a, b in pairwise(groups):
            if b.speaker.kind == "someone" or _who(a.speaker) == _who(b.speaker):
                continue
            if self.cloud is not None and self.cloud.placed(a.words, b.words):
                continue  # V-32: the cloud's tags put the change here
            change = float(b.words[0][1])
            best_k, best_gap = None, change - _end(a.words[-1], s.max_word_s)
            best_gap = max(best_gap, s.snap_gap_s - 1e-9)
            for k in range(len(a.words) - 1, 0, -1):
                if change - float(a.words[k][1]) > s.snap_max_s:
                    break
                gap = float(a.words[k][1]) - _end(a.words[k - 1], s.max_word_s)
                if gap > best_gap:
                    best_k, best_gap = k, gap
            if best_k is not None:
                b.words[:0] = a.words[best_k:]
                b.evidence[:0] = a.evidence[best_k:]
                del a.words[best_k:], a.evidence[best_k:]
        return groups

    def _change_time(self, spk: Speaker | None, now: float) -> float:
        """When a speaker change decided now really happened (V-31).

        Light-ASD scores the newest `asd_score_s` of lips and sound a few times a second,
        so it hears a new talker `asd_switch_lag_s` or so after they start. A switch from
        one face to another that Light-ASD made is dated back by that much, so a reply's
        first words (often without a pause for `_snap`) don't stay the previous talker's.
        """
        prev = self.timeline[-1] if self.timeline else None
        if (
            self.s.asd_switch_lag_s > 0
            and prev is not None
            and spk is not None
            and spk.kind == "face"
            and prev[1] is not None
            and prev[1].kind == "face"
            and prev[1].track_id != spk.track_id
            and spk.track_id in self.asd_gate.covered(self, now)
        ):
            return max(now - self.s.asd_switch_lag_s, prev[0] + 1e-3)
        return now

    def take_retractions(self) -> list[CaptionRetract]:
        """Segment ids to retract since the last call (the service publishes them)."""
        out, self.retractions = self.retractions, []
        return out

    # ---------------- tick ----------------
    def tick(self, now: float) -> tuple[Scene, list[Caption], VoiceHarvest | None]:
        self._assess(now)
        self._claim(now)
        self._decided_by = None
        spk, r = self.asd_gate.decide(self, now)  # V-22: decide(), Light-ASD for faces it scores
        if not _same(spk, self.current):
            self._current_since = now
        self.current = spk
        self._current_in_time = r
        if not self.timeline or not _same(self.timeline[-1][1], spk):
            self.timeline.append((self._change_time(spk, now), spk))
        while self.timeline and now - self.timeline[0][0] > 30:
            self.timeline.popleft()
        if self.cloud is not None:
            self.cloud.note(self, now, spk)  # V-32: faces talking, for binding cloud tags

        captions = []
        for utt in list(self.pending):
            p = self.pending[utt]
            out = self._captions_for(p.event, now, p.first_seen)
            if out is not None:
                captions += out
                del self.pending[utt]
        for utt in [u for u, m in self.utts.items() if now - m.t > self.s.utterance_memory_s]:
            del self.utts[utt]  # its final never came (audio restarted): stop tracking it

        # A voice is learnt only from a face talking on its own evidence right now (not held,
        # not given the speech by its voice) and clearly in time with the sound.
        confident = None
        if (
            spk is not None
            and spk.kind == "face"
            and self._decided_by == "talking"
            and r is not None
            and r >= self.s.harvest_min_corr
        ):
            confident = voice_id(spk.person_id, spk.track_id)
        elif spk is not None and spk.kind == "someone":
            confident = self._offscreen_voice(now)
        harvest = self.harvester.update(now, confident)
        if harvest is not None:
            self.harvested.add(harvest.person_id)
        return self.scene(), captions, harvest

    def _offscreen_voice(self, now: float) -> str | None:
        """The "offscreen-N" id to learn this speech under, or None if not clearly off screen.

        Clearly off screen: at least one face is visible, every visible face's mouth is
        sampled steadily and still (below its probable line), Light-ASD calls every face it
        scores silent, and no face talked earlier in this run of speech (V-31: a talker whose
        hand hides their mouth talks on, and their voice must not be learnt as someone off
        screen, which would then take their own speech away from them). The id is the
        off-screen voice this utterance already matched, or a new one for this run of
        speech. Speech that matched anyone else, or came close to a known print, isn't learnt.
        """
        if not self.s.learn_offscreen:
            return None
        fresh = [tr for tr in self.tracks.values() if now - tr.t <= 0.5]
        if not fresh or any(not tr.measured or tr.stirring for tr in fresh):
            return None
        covered = self.asd_gate.covered(self, now)
        if any(asd.score >= self.s.asd_off for asd in covered.values()):
            return None
        start = self.speech_start if self.speech_start is not None else now
        if self._earned[0] == start and any(v > 0 for v in self._earned[1].values()):
            return None
        match = self._latest_voice_match(now)
        if match is not None:
            _, _, pid, score = match
            if pid is not None:
                if not pid.startswith("offscreen-") or pid in self.claimed:
                    return None
                return pid
            if score >= self.s.voice_reject:
                return None  # close to a print already known: not clearly a new voice
        if self._offscreen_run is None or self._offscreen_run[0] != start:
            self._offscreen_n += 1
            self._offscreen_run = (start, f"offscreen-{self._offscreen_n}")
        return self._offscreen_run[1]

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
