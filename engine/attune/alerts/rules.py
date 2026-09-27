"""Deterministic alert decisions and acknowledgement lifecycle."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from uuid import uuid4

from .rhythm import RhythmEvidence

# Kinds that fire on a single sound, and the config key for the rest between their alerts.
REST_KEYS = {"doorbell": "doorbell_rest_s", "knock": "knock_rest_s"}


@dataclass(frozen=True)
class Sound:
    """An everyday sound a deaf or hard-of-hearing wearer wants to know about (A-40).

    `labels` are AudioSet classes (the loudest one counts), `score` the default threshold,
    `hits` how many of the last three windows must pass it (1 = a single sound), `rest_s` the
    least gap between two alerts, `clear_s` how long the alert stays after the sound stops, and
    `block` the classes that veto it (music playing a siren, say). Thresholds come from
    [alerts] <kind>_score / <kind>_rest_s when set; scripts/train_sounds.py tunes them."""

    labels: tuple[str, ...]
    score: float
    rest_s: float
    clear_s: float
    haptic: str = "BELL"
    hits: int = 1
    block: tuple[str, ...] = ("Music",)


SOUNDS: dict[str, Sound] = {
    "siren": Sound(
        (
            "Siren",
            "Police car (siren)",
            "Ambulance (siren)",
            "Fire engine, fire truck (siren)",
            "Civil defense siren",
            "Emergency vehicle",
        ),
        0.3,
        30,
        8,
        hits=2,
    ),
    "horn": Sound(
        (
            "Vehicle horn, car horn, honking",
            "Air horn, truck horn",
            "Train horn",
            "Reversing beeps",
        ),
        0.3,
        6,
        4,
    ),
    "scream": Sound(("Screaming",), 0.35, 10, 6),
    "glass": Sound(("Shatter", "Breaking", "Smash, crash"), 0.25, 10, 6),
    "baby": Sound(("Baby cry, infant cry",), 0.3, 20, 8),
    "dog": Sound(("Bark", "Yip", "Howl", "Whimper (dog)"), 0.3, 20, 5, haptic="NAME"),
    "phone": Sound(("Telephone bell ringing", "Ringtone"), 0.35, 15, 5, haptic="NAME", block=()),
    "timer": Sound(
        ("Alarm clock", "Microwave oven", "Beep, bleep", "Buzzer"),
        0.4,
        20,
        5,
        haptic="NAME",
        hits=2,
        block=("Music", "Speech"),
    ),
    "water": Sound(
        ("Water tap, faucet", "Sink (filling or washing)", "Bathtub (filling or washing)"),
        0.35,
        60,
        6,
        haptic="NAME",
        hits=3,
    ),
}


def sound_score(scores: dict, kind: str) -> float:
    return max((scores.get(label, 0.0) for label in SOUNDS[kind].labels), default=0.0)


class AlertRules:
    """Fuse recent model scores with independent alarm rhythm evidence."""

    def __init__(self, config: dict, side_db: float):
        self.cfg, self.side_db = config, side_db
        self.history = deque(maxlen=3)
        self.active: dict = {}
        self.last_start = dict.fromkeys([*REST_KEYS, *SOUNDS], float("-inf"))
        self.watch_until = float("-inf")
        self.hits = {kind: deque(maxlen=3) for kind in SOUNDS}
        # A-41: kind -> time its mute ends. Holding the touch sensor on an alert mutes that kind
        # of sound for snooze_s (an hour); a pause, a forgotten session or a restart keeps it
        # until then only while the engine runs.
        self.snoozed: dict[str, float] = {}

    def muted(self, kind: str, t: float) -> bool:
        until = self.snoozed.get(kind)
        if until is not None and t >= until:
            del self.snoozed[kind]
            return False
        return until is not None

    def threshold(self, kind: str) -> float:
        return float(self.cfg.get(f"{kind}_score", SOUNDS[kind].score))

    def rest_s(self, kind: str) -> float:
        if kind in REST_KEYS:
            return self.cfg[REST_KEYS[kind]]
        if kind in SOUNDS:
            return float(self.cfg.get(f"{kind}_rest_s", SOUNDS[kind].rest_s))
        return 0.0

    def heard(self, scores: dict) -> bool:
        """Whether any single-sound kind (doorbell, knock, the SOUNDS) passes its threshold:
        these are broadband, so the service takes their side from the sensor levels."""
        bell = max(scores.get("Doorbell", 0), scores.get("Ding-dong", 0))
        return (
            bell >= self.cfg["doorbell_score"]
            or scores.get("Knock", 0) >= self.cfg["knock_score"]
            or any(sound_score(scores, k) >= self.threshold(k) for k in SOUNDS)
        )

    def side(self, left: float, right: float) -> str:
        delta = 20 * math.log10(max(left, 1e-6) / max(right, 1e-6))
        return "left" if delta >= self.side_db else "right" if delta <= -self.side_db else "none"

    def _event(self, kind: str, state: str) -> tuple[str, dict]:
        a = self.active[kind]
        return "alert", {
            "alert_id": a["id"],
            "kind": kind,
            "side": a["side"],
            "confidence": a["score"],
            "state": state,
        }

    def _pattern(self, kind: str) -> tuple[str, dict]:
        return "hw.pattern", {
            "name": {"smoke": "T3", "co": "T4", "doorbell": "BELL", "knock": "BELL"}.get(kind)
            or SOUNDS[kind].haptic,
            "side": {"left": "L", "right": "R", "none": "B"}[self.active[kind]["side"]],
        }

    def evaluate(
        self,
        t: float,
        scores: dict,
        rhythm: RhythmEvidence,
        levels: tuple | None = None,
        motor_on: bool = False,
    ) -> list:
        """Ignore motor-contaminated windows, then apply the plan's firing table."""
        if motor_on:
            # The rig's own taps drown the room, so this window can neither confirm an alarm
            # (the taps play the alarm's own rhythm) nor show that it stopped. It must not run
            # down the quiet-clear timer either: a playing alarm stays on until "Got it", or
            # until the room is heard quiet once the taps have stopped (A-28).
            self.history.clear()
            for a in self.active.values():
                a["seen"] = max(a["seen"], t)
            return self.tick(t)
        smoke = max(
            scores.get("Smoke detector, smoke alarm", scores.get("Smoke detector", 0)),
            scores.get("Fire alarm", 0),
        )
        self.history.append(smoke >= self.cfg["smoke_score"])
        fires = {}
        if (sum(self.history) >= 2 and rhythm.beeps >= 2) or rhythm.t3_cycles >= 2:
            fires["smoke"] = max(smoke, 1.0 if rhythm.t3_cycles >= 2 else smoke)
        if rhythm.t4_cycles >= 2:
            fires["co"] = 1.0
        bell = max(scores.get("Doorbell", 0), scores.get("Ding-dong", 0))
        if (
            bell >= self.cfg["doorbell_score"]
            and scores.get("Speech", 0) < self.cfg["speech_music_block"]
            and scores.get("Music", 0) < self.cfg["speech_music_block"]
        ):
            fires["doorbell"] = bell
        # A-30: a knock on the door. Music blocks it (drums), speech doesn't: people call out
        # while they knock, and knocks under speech still scored well above the threshold.
        knock = scores.get("Knock", 0)
        if (
            knock >= self.cfg["knock_score"]
            and scores.get("Music", 0) < self.cfg["speech_music_block"]
        ):
            fires["knock"] = knock
        alarm = "smoke" in fires or "co" in fires or "smoke" in self.active or "co" in self.active
        # pure beeps at a smoke/CO alarm's pitch: wait for the rhythm check before calling it a timer
        alarm = alarm or rhythm.tone_on or rhythm.beeps > 0
        for kind, sound in SOUNDS.items():
            score = sound_score(scores, kind)
            hit = score >= self.threshold(kind) and all(
                scores.get(label, 0) < self.cfg["speech_music_block"] for label in sound.block
            )
            if kind == "timer" and (alarm or smoke >= self.cfg["watch_score"]):
                hit = False  # a smoke or CO alarm beeps too: that one is the alert
            self.hits[kind].append(hit)
            if hit and sum(self.hits[kind]) >= sound.hits:
                fires[kind] = score
        fires = {kind: score for kind, score in fires.items() if not self.muted(kind, t)}
        output = []
        if ("smoke" in fires or "co" in fires) and "timer" in self.active:
            output.append(self._event("timer", "clear"))  # it was the alarm all along
            del self.active["timer"]
        if (
            self.cfg["watch_score"] <= smoke < self.cfg["smoke_score"]
            and not self.muted("smoke", t)
            and t >= self.watch_until
            and "smoke" not in self.active
        ):
            self.watch_until = t + self.cfg["watch_s"]
            output.append(
                (
                    "status.part",
                    {
                        "part": "alerts.watch",
                        "ok": True,
                        "detail": "watching for alarm rhythm",
                        "metrics": {"expires_t": self.watch_until},
                    },
                )
            )
        for kind, score in fires.items():
            # A matching cadence remains valid during its expected pause, but
            # that silence must not postpone the quiet-clear deadline.
            seen = t
            if kind == "co" or (kind == "smoke" and smoke < self.cfg["smoke_score"]):
                seen -= rhythm.quiet_s
            if kind not in self.active:
                if kind in self.last_start and t - self.last_start[kind] < self.rest_s(kind):
                    continue
                self.active[kind] = {
                    "id": str(uuid4()),
                    "side": self.side(*levels) if levels is not None else "none",
                    "score": score,
                    "seen": seen,
                    "ack": None,
                }
                output.extend([self._event(kind, "start"), self._pattern(kind)])
                if kind in self.last_start:
                    self.last_start[kind] = t
            else:
                a = self.active[kind]
                a["seen"], a["score"] = seen, score
                new_side = self.side(*levels) if levels is not None else a["side"]
                # a heard side sticks: a quiet window ("none") never takes the arrow away
                if new_side not in (a["side"], "none") and a["ack"] is None:
                    a["side"] = new_side
                    output.append(self._event(kind, "update"))
                if a["ack"] is not None and seen - a["ack"] >= self.cfg["realert_s"]:
                    a["ack"] = None
                    output.extend([self._event(kind, "start"), self._pattern(kind)])
        output.extend(self.tick(t))
        return output

    def snooze(self, alert_id: str, t: float) -> list:
        """Stop this alert and mute its kind of sound for snooze_s: the same sound again within
        that time raises nothing. The rig confirms with a short OK."""
        for kind, a in list(self.active.items()):
            if a["id"] == alert_id:
                seconds = float(self.cfg.get("snooze_s", 3600))
                self.snoozed[kind] = t + seconds
                topic, event = self._event(kind, "acknowledged")
                del self.active[kind]
                if kind in self.hits:
                    self.hits[kind].clear()
                if kind == "smoke":
                    self.history.clear()
                event["snooze_s"] = seconds
                return [
                    (topic, event),
                    ("hw.stop", {}),
                    ("hw.pattern", {"name": "OK", "side": "B"}),
                ]
        return []

    def acknowledge(self, alert_id: str, t: float) -> list:
        for kind, a in self.active.items():
            if a["id"] == alert_id and a["ack"] is None:
                a["ack"] = t
                return [self._event(kind, "acknowledged"), ("hw.stop", {})]
        return []

    def tick(self, t: float) -> list:
        output = []
        for kind in list(self.active):
            quiet = SOUNDS[kind].clear_s if kind in SOUNDS else self.cfg["clear_quiet_s"]
            if t - self.active[kind]["seen"] >= quiet:
                output.append(self._event(kind, "clear"))
                del self.active[kind]
        if output and not self.active:
            output.append(("hw.stop", {}))
        return output

    def clear(self) -> list:
        output = [self._event(kind, "clear") for kind in self.active]
        self.active.clear()
        self.history.clear()
        self.last_start = dict.fromkeys([*REST_KEYS, *SOUNDS], float("-inf"))
        self.watch_until = float("-inf")
        for hits in self.hits.values():
            hits.clear()
        return output + [("hw.stop", {})]
