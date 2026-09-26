"""Bus-facing local language jobs, with session-safe asynchronous completion."""

from __future__ import annotations

import logging
from collections import deque

from attune.audio.runtime import Worker, engine_clock

from . import describe, replies, translate
from .client import OllamaClient
from .describe import Descriptions
from .names import PROMPT, Names
from .names import SCHEMA as NAME_SCHEMA

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, bus, config, *, client=None):
        self.bus, self.config = bus, config
        self.client = client or OllamaClient(config["llm"])
        self.worker = Worker(bus, "llm", self._handle, self._tick)
        self.worker.health = self._health
        self.names, self.descriptions = Names(config["llm"]), Descriptions()
        self.context = deque(maxlen=config["llm"].get("reply_context_lines", 6))
        self.seen = deque(maxlen=256)
        self.translation = True
        self.paused = False
        self.lost = set()
        self.jobs = []
        self.track_generations = {}
        self._state_generation = self.worker.generation
        self._error = ""
        # A-23: suggestions are asked for once the talk pauses, not on every final
        self.reply_delay_s = float(config["llm"].get("reply_delay_s", 0.0))
        self._reply_due: tuple[float, int] | None = None

    def _health(self) -> dict:
        warm = getattr(self.client, "warm", False)
        if warm:
            self._error = ""
        error = getattr(self.client, "error", "") or self._error
        metrics = {"warm": warm, "pending": len(self.jobs)}
        stats = getattr(self.client, "stats", None)
        if stats:
            metrics["jobs"] = {kind: dict(s) for kind, s in list(stats.items())}
        return {
            "ok": warm and not error,
            "detail": error or ("ready" if warm else "warming local language model"),
            "metrics": metrics,
        }

    def start(self) -> None:
        self.clock = engine_clock(self.config)
        for topic in (
            "caption",
            "vision.appearance",
            "vision.track_lost",
            "touch.action",
            "command",
            "session.forget",
            "paused",
            "person.changed",
        ):
            self.worker.subscribe(topic)
        self.client.start()
        self.worker.start()

    def _queue(
        self, kind: str, messages: list, schema: dict, source: dict, generation: int
    ) -> None:
        if kind in {"replies", "descriptions"}:
            retained = []
            for old in self.jobs:
                if old[1] == kind and (
                    kind == "replies" or old[2]["track_id"] == source["track_id"]
                ):
                    old[0].cancel()
                else:
                    retained.append(old)
            self.jobs = retained
        future = self.client.submit(kind, messages, schema)
        self.jobs.append((future, kind, source, generation))

    def _clear(self) -> None:
        self.client.cancel_pending()
        self.jobs.clear()
        self.context.clear()
        self.seen.clear()
        self.names.forget()
        self.descriptions.labels.clear()
        self.lost.clear()
        self.track_generations.clear()
        self._state_generation = self.worker.generation
        self._error = ""
        self._reply_due = None

    def _handle(self, topic: str, e: dict, generation: int) -> None:
        if generation != self.worker.generation and topic not in {
            "session.forget",
            "paused",
            "person.changed",
        }:
            return
        if self._state_generation != self.worker.generation:
            self._clear()
        if topic == "session.forget" or topic == "person.changed":
            self._clear()
        elif topic == "paused":
            self.paused = e["paused"]
            self._clear()
        elif topic == "vision.track_lost":
            track = e["track_id"]
            self.lost.add(track)
            self.track_generations[track] = self.track_generations.get(track, 0) + 1
            self.descriptions.labels.pop(track, None)
            for key, p in list(self.names.pending.items()):
                if p["track_id"] == track:
                    self.names.pending.pop(key)
                    self.worker.publish("name.proposal", p | {"state": "expired"}, generation)
        elif topic == "touch.action" and e["target"] == "name":
            self._answer(e["id"], e["accept"], generation)
        elif topic == "command":
            args = e.get("args", {})
            if e["name"] == "name.answer":
                self._answer(args["proposal_id"], args["accept"], generation)
            elif e["name"] == "switch.set" and args["key"] == "translation":
                self.translation = bool(args["value"])
        elif (
            not self.paused
            and topic == "caption"
            and e.get("final")
            and e["utt_id"] not in self.seen
        ):
            self.seen.append(e["utt_id"])
            self.context.append(e["text"])
            if self.translation and e["lang"] not in {"en", "und", ""}:
                self._queue("translation", translate.messages(e), translate.SCHEMA, e, generation)
            if self.names.eligible(e):
                track = e["speaker"]["track_id"]
                self._queue(
                    "names",
                    [{"role": "system", "content": PROMPT}, {"role": "user", "content": e["text"]}],
                    NAME_SCHEMA,
                    e | {"track_generation": self.track_generations.get(track, 0)},
                    generation,
                )
            if self.reply_delay_s > 0:
                self._reply_due = (self.clock() + self.reply_delay_s, generation)
            else:
                self._queue_reply(generation)
        elif not self.paused and topic == "vision.appearance":
            self.lost.discard(e["track_id"])
            self._queue(
                "descriptions",
                describe.messages(
                    e["crop"], int(self.config["llm"].get("description_max_px", 224))
                ),
                describe.SCHEMA,
                {
                    "track_id": e["track_id"],
                    "track_generation": self.track_generations.get(e["track_id"], 0),
                },
                generation,
            )

    def _queue_reply(self, generation: int) -> None:
        self._reply_due = None
        self._queue("replies", replies.messages(list(self.context)), replies.SCHEMA, {}, generation)

    def _answer(self, key: str, accept: bool, generation: int) -> None:
        if self.paused or generation != self.worker.generation:
            return
        event = self.names.answer(key, accept, self.clock())
        if event:
            self.worker.publish("name.proposal", event, generation)
            if event["state"] in {"confirmed", "rejected"}:
                self.worker.publish(
                    "hw.pattern",
                    {"name": "OK" if event["state"] == "confirmed" else "NO", "side": "R"},
                    generation,
                )

    def _tick(self) -> None:
        if self._state_generation != self.worker.generation or self.paused:
            return
        for event in self.names.expire(self.clock()):
            self.worker.publish("name.proposal", event, self._state_generation)
        due = self._reply_due
        if due is not None and self.clock() >= due[0] and due[1] == self.worker.generation:
            self._queue_reply(due[1])
        pending = []
        for future, kind, source, generation in self.jobs:
            if not future.done():
                pending.append((future, kind, source, generation))
                continue
            if generation != self.worker.generation or future.cancelled():
                continue
            try:
                answer = future.result()
                event = None
                if kind == "translation" and self.translation:
                    topic, event = "caption.translation", translate.result(source, answer)
                elif kind == "names" and self._current_track(source, source["speaker"]["track_id"]):
                    topic, event = "name.proposal", self.names.propose(source, answer, self.clock())
                    if event:
                        self.worker.publish("hw.pattern", {"name": "NAME", "side": "R"}, generation)
                elif kind == "replies":
                    topic, event = "reply.suggestions", replies.result(answer)
                elif kind == "descriptions" and self._current_track(source, source["track_id"]):
                    topic, event = (
                        "vision.description",
                        self.descriptions.result(source["track_id"], answer),
                    )
                if event:
                    self.worker.publish(topic, event, generation)
                self._error = ""
            except Exception:
                self._error = "local language job failed"
                logger.exception("local language job failed")
        self.jobs = pending

    def _current_track(self, source: dict, track: int) -> bool:
        return track not in self.lost and source["track_generation"] == self.track_generations.get(
            track, 0
        )

    def stop(self) -> None:
        self.client.stop()
        self.worker.stop()
        if not self.worker.thread or not self.worker.thread.is_alive():
            self._clear()
