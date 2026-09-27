"""Alert workers: audio windows, tone-only direction and motor suppression."""

from __future__ import annotations

import logging
from collections import deque

import numpy as np

from attune.audio.runtime import Worker, engine_clock

from .rhythm import RhythmDetector
from .rules import AlertRules
from .sound_model import CLIP_SAMPLES, SoundModel

logger = logging.getLogger(__name__)


class AlertService:
    def __init__(self, bus, config, *, model=None):
        self.bus, self.config, self.model = bus, config, model
        self.worker = Worker(bus, "alerts", self._handle, self._tick)
        self.rhythm = RhythmDetector(config["rhythm"])
        self.hop = round(config["alerts"]["hop_s"] * 32000)
        if (
            config["alerts"]["window_s"] != 1.0
            or not 0 < self.hop <= 32000
            or self.hop % self.rhythm.size
            or 32000 % self.rhythm.size
        ):
            raise ValueError("alerts require a 1 s window and whole rhythm frames per hop")
        self.rules = AlertRules(config["alerts"], config["fusion"]["side_db"])
        self.audio = np.empty(0, np.float32)
        self.recent = np.empty(0, np.float32)  # up to 10 s of context for the sound model
        self.levels = deque(maxlen=200)
        self.tone_levels = deque()
        self.rhythm_end = None
        self.enabled = True
        self.paused = False
        self.last_t = None

    def start(self) -> None:
        """Load the local scorer and subscribe without blocking bus callbacks."""
        self.clock = engine_clock(self.config)
        if self.model is None:
            try:
                self.model = SoundModel(self.config["sound_model"])
            except Exception:
                self.worker.error = "sound model unavailable; rhythm-only alerts remain active"
                logger.exception("Sound classifier unavailable; starting rhythm-only alerts")
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
        self.recent = np.empty(0, np.float32)
        self.rhythm.reset()
        self.rhythm_end = None
        self.tone_levels.clear()
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
                self.recent = np.empty(0, np.float32)
                self.rhythm.reset()
                self.rhythm_end = None
                self.tone_levels.clear()
                self.rules.history.clear()
            self.last_t = e["t"] + len(samples) / 32000
            self.audio = np.concatenate((self.audio, samples))
            self.recent = np.concatenate((self.recent, samples))[-(CLIP_SAMPLES + 32000) :]
            while len(self.audio) >= 32000:
                window = self.audio[:32000]
                end = self.last_t - (len(self.audio) - 32000) / 32000
                levels = [v for v in self.levels if end - 1 <= v["t"] <= end]
                motor = any(v["motor_on"] for v in levels)
                if motor:
                    self.rhythm.reset()
                    self.rhythm_end = None
                    self.tone_levels.clear()
                    evidence = self.rhythm.evidence
                    direction = None
                    scores = {}
                else:
                    # Score tone direction on the same 10 ms frames, not the whole window.
                    frame_size = self.rhythm.size
                    start = 0 if self.rhythm_end is None else round((self.rhythm_end - end + 1) * 32000)
                    for offset in range(start, 32000, frame_size):
                        evidence = self.rhythm.feed(window[offset : offset + frame_size])
                        t = end - 1 + offset / 32000
                        current = [v for v in levels if v["t"] <= t]
                        if evidence.tone_on and current:
                            self.tone_levels.append((t, current[-1]["left"], current[-1]["right"]))
                    self.rhythm_end = end
                    while self.tone_levels and self.tone_levels[0][0] < end - 1:
                        self.tone_levels.popleft()
                    pairs = [(left, right) for _, left, right in self.tone_levels]
                    scores = {}
                    if self.model is not None:
                        try:
                            ahead = len(self.audio) - 32000  # samples after this window
                            context = self.recent[: len(self.recent) - ahead]
                            scores = self.model.score(context[-CLIP_SAMPLES:])
                        except Exception:
                            self.worker.error = "sound model failed; rhythm-only alerts remain active"
                            logger.exception("Sound classification failed; preserving rhythm evidence")
                        else:
                            if self.worker.error.startswith("sound model"):
                                self.worker.error = ""
                    # Doorbells and knocks are broadband; use the event window's sensor balance.
                    cfg = self.config["alerts"]
                    bell = max(scores.get("Doorbell", 0), scores.get("Ding-dong", 0))
                    if not pairs and (
                        bell >= cfg["doorbell_score"]
                        or scores.get("Knock", 0) >= cfg["knock_score"]
                    ):
                        pairs = [(v["left"], v["right"]) for v in levels]
                    direction = tuple(np.mean(pairs, axis=0)) if pairs else None
                self._emit(self.rules.evaluate(end, scores, evidence, direction, motor), generation)
                self.audio = self.audio[self.hop:]

    def stop(self) -> None:
        """Cancel inference output and request patterns to stop."""
        self.worker.stop()
        self.bus.publish("hw.stop", {})
