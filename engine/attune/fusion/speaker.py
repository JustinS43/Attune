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
speaker before showing as "Someone".

This class is pure logic with an explicit `now`, so tests can drive it with
simulated events; FusionService (service.py) runs it on the bus.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from ..vision.settings import FusionSettings
from ..vision.types import Caption, FaceState, Offscreen, Scene, Speaker, VoiceHarvest, get
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


class SpeakerFusion:
    def __init__(self, settings: FusionSettings | None = None):
        self.s = settings or FusionSettings()
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
        self.harvester = Harvester(self.s.harvest_after_s)

    # ---------------- inputs ----------------
    def on_tracks(self, ev) -> None:
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
        words = [tuple(w) for w in (get(ev, "words") or [])]
        t_start = float(get(ev, "t_start", now))
        t_end = float(get(ev, "t_end", now))
        if not words:
            words = [(str(get(ev, "text", "")), t_start, t_end)]
        first = self.speaker_at((words[0][1] + words[0][2]) / 2)
        if (
            first is None or first.kind == "someone"
        ) and now - first_seen < self.s.first_words_wait_ms / 1000:
            return None  # wait a little for a speaker
        groups: list[tuple[Speaker, list]] = []
        for w in words:
            spk = self.speaker_at((w[1] + w[2]) / 2) or Speaker(
                "someone", label="Someone", side="none"
            )
            if groups and _same(groups[-1][0], spk):
                groups[-1][1].append(w)
            else:
                groups.append((spk, [w]))
        utt = str(get(ev, "utt_id"))
        final = bool(get(ev, "final", False))
        lang = get(ev, "lang")
        if len(groups) == 1:
            return [Caption(utt, groups[0][0], str(get(ev, "text", "")), final, lang, words)]
        return [
            Caption(
                utt if i == 0 else f"{utt}.{i}",
                spk,
                " ".join(w[0] for w in ws).strip(),
                final,
                lang,
                ws,
            )
            for i, (spk, ws) in enumerate(groups)
        ]

    # ---------------- tick ----------------
    def tick(self, now: float) -> tuple[Scene, list[Caption], VoiceHarvest | None]:
        spk, r = self.decide(now)
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
