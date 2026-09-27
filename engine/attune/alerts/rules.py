"""Deterministic alert decisions and acknowledgement lifecycle."""

from __future__ import annotations

import math
from collections import deque
from uuid import uuid4

from .rhythm import RhythmEvidence


class AlertRules:
    """Fuse recent model scores with independent alarm rhythm evidence."""

    def __init__(self, config: dict, side_db: float):
        self.cfg, self.side_db = config, side_db
        self.history = deque(maxlen=3)
        self.active: dict = {}
        self.last_bell = float("-inf")
        self.watch_until = float("-inf")

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
            "name": {"smoke": "T3", "co": "T4", "doorbell": "BELL"}[kind],
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
        output = []
        if (
            self.cfg["watch_score"] <= smoke < self.cfg["smoke_score"]
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
                if kind == "doorbell" and t - self.last_bell < self.cfg["doorbell_rest_s"]:
                    continue
                self.active[kind] = {
                    "id": str(uuid4()),
                    "side": self.side(*levels) if levels is not None else "none",
                    "score": score,
                    "seen": seen,
                    "ack": None,
                }
                output.extend([self._event(kind, "start"), self._pattern(kind)])
                if kind == "doorbell":
                    self.last_bell = t
            else:
                a = self.active[kind]
                a["seen"], a["score"] = seen, score
                new_side = self.side(*levels) if levels is not None else a["side"]
                if new_side != a["side"] and a["ack"] is None:
                    a["side"] = new_side
                    output.append(self._event(kind, "update"))
                if a["ack"] is not None and seen - a["ack"] >= self.cfg["realert_s"]:
                    a["ack"] = None
                    output.extend([self._event(kind, "start"), self._pattern(kind)])
        output.extend(self.tick(t))
        return output

    def acknowledge(self, alert_id: str, t: float) -> list:
        for kind, a in self.active.items():
            if a["id"] == alert_id and a["ack"] is None:
                a["ack"] = t
                return [self._event(kind, "acknowledged"), ("hw.stop", {})]
        return []

    def tick(self, t: float) -> list:
        output = []
        for kind in list(self.active):
            if t - self.active[kind]["seen"] >= self.cfg["clear_quiet_s"]:
                output.append(self._event(kind, "clear"))
                del self.active[kind]
        if output and not self.active:
            output.append(("hw.stop", {}))
        return output

    def clear(self) -> list:
        output = [self._event(kind, "clear") for kind in self.active]
        self.active.clear()
        self.history.clear()
        self.last_bell = self.watch_until = float("-inf")
        return output + [("hw.stop", {})]
