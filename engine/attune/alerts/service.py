"""Alert workers: audio windows, tone-only direction and motor suppression."""

from __future__ import annotations

from collections import deque

import numpy as np

from attune.audio.runtime import Worker, engine_clock

from .rhythm import RhythmDetector
from .rules import AlertRules
from .sound_model import SoundModel


class AlertService:
    def __init__(self, bus, config, *, model=None):
        self.bus, self.config, self.model = bus, config, model
        self.worker = Worker(bus, "alerts", self._handle, self._tick)
        self.rhythm = RhythmDetector(config["rhythm"])
        self.rules = AlertRules(config["alerts"], config["fusion"]["side_db"])
        self.audio = np.empty(0, np.float32)
        self.levels = deque(maxlen=200)
        self.enabled = True
        self.paused = False
        self.last_t = None
        self.latest_t = 0.0

    def start(self) -> None:
        """Load the local scorer and subscribe without blocking bus callbacks."""
        self.clock = engine_clock(self.config)
        self.model = self.model or SoundModel(self.config["sound_model"])
        for topic in (
            "audio.block",
            "sensors.levels",
            "touch.action",
            "command",
            "session.forget",
            "paused",
        ):
            self.worker.subscribe(topic)
        self.worker.start()

    def _emit(self, events: list, generation=None) -> None:
        for topic, event in events:
            self.worker.publish(topic, event, generation)

    def _reset(self) -> None:
        self.audio = np.empty(0, np.float32)
        self.rhythm.reset()
        self.last_t = None
        self.levels.clear()
        self._emit(self.rules.clear())

    def _tick(self) -> None:
        self._emit(self.rules.tick(self.clock()))

    def _handle(self, topic: str, e: dict, generation: int) -> None:
        if topic == "session.forget":
            self._reset()
        elif topic == "paused":
            self.paused = e["paused"]
            self._reset()
        elif topic == "sensors.levels":
            self.levels.append(e)
        elif topic == "touch.action" and e["target"] == "alert":
            self._emit(self.rules.acknowledge(e["id"], self.clock()))
        elif topic == "command":
            args = e.get("args", {})
            if e["name"] == "alert.ack":
                self._emit(self.rules.acknowledge(args["alert_id"], self.clock()))
            elif e["name"] == "switch.set" and args["key"] == "alerts":
                self.enabled = bool(args["value"])
                self._reset()
        elif (
            topic == "audio.block"
            and e["sample_rate"] == 32000
            and self.enabled
            and not self.paused
        ):
            samples = np.asarray(e["samples"], dtype=np.float32)
            if self.last_t is not None and abs(e["t"] - self.last_t) > 1.5 / 32000:
                self.audio = np.empty(0, np.float32)
                self.rhythm.reset()
                self.rules.history.clear()
            self.last_t = e["t"] + len(samples) / 32000
            self.audio = np.concatenate((self.audio, samples))
            while len(self.audio) >= 32000:
                window = self.audio[:32000]
                end = self.last_t - (len(self.audio) - 32000) / 32000
                levels = [v for v in self.levels if end - 1 <= v["t"] <= end]
                motor = any(v["motor_on"] for v in levels)
                hop = round(self.config["alerts"]["hop_s"] * 32000)
                if motor:
                    self.rhythm.reset()
                    evidence = self.rhythm.evidence
                    direction = (0, 0)
                    scores = {}
                else:
                    # Score tone direction on the same 10 ms frames, not the whole window.
                    pairs = []
                    frame_size = self.rhythm.size
                    for offset in range(0, hop, frame_size):
                        evidence = self.rhythm.feed(window[offset : offset + frame_size])
                        t = end - 1 + offset / 32000
                        current = [v for v in levels if v["t"] <= t]
                        if evidence.tone_on and current:
                            pairs.append((current[-1]["left"], current[-1]["right"]))
                    scores = self.model.score(window)
                    # Doorbells are broadband; use the event window's sensor balance.
                    if (
                        not pairs
                        and max(scores.get("Doorbell", 0), scores.get("Ding-dong", 0))
                        >= self.config["alerts"]["doorbell_score"]
                    ):
                        pairs = [(v["left"], v["right"]) for v in levels]
                    direction = tuple(np.mean(pairs, axis=0)) if pairs else (0, 0)
                self._emit(self.rules.evaluate(end, scores, evidence, direction, motor), generation)
                self.audio = self.audio[hop:]

    def stop(self) -> None:
        """Cancel inference output and request patterns to stop."""
        self.worker.stop()
        self.bus.publish("hw.stop", {})
