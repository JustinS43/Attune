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
        self.names, self.descriptions = Names(config["llm"]), Descriptions()
        self.context = deque(maxlen=config["llm"]["reply_context_lines"])
        self.seen = deque(maxlen=256)
        self.translation = True
        self.paused = False
        self.lost = set()
        self.jobs = []

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

    def _handle(self, topic: str, e: dict, generation: int) -> None:
        if topic == "session.forget" or topic == "person.changed":
            self._clear()
        elif topic == "paused":
            self.paused = e["paused"]
            self._clear()
        elif topic == "vision.track_lost":
            track = e["track_id"]
            self.lost.add(track)
            self.descriptions.labels.pop(track, None)
            for key, p in list(self.names.pending.items()):
                if p["track_id"] == track:
                    self.names.pending.pop(key)
                    self.worker.publish("name.proposal", p | {"state": "expired"})
        elif topic == "touch.action" and e["target"] == "name":
            self._answer(e["id"], e["accept"])
        elif topic == "command":
            args = e.get("args", {})
            if e["name"] == "name.answer":
                self._answer(args["proposal_id"], args["accept"])
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
                self._queue(
                    "names",
                    [{"role": "system", "content": PROMPT}, {"role": "user", "content": e["text"]}],
                    NAME_SCHEMA,
                    e,
                    generation,
                )
            self._queue(
                "replies", replies.messages(list(self.context)), replies.SCHEMA, {}, generation
            )
        elif not self.paused and topic == "vision.appearance":
            self.lost.discard(e["track_id"])
            self._queue(
                "descriptions",
                describe.messages(e["crop"]),
                describe.SCHEMA,
                {"track_id": e["track_id"]},
                generation,
            )

    def _answer(self, key: str, accept: bool) -> None:
        event = self.names.answer(key, accept, self.clock())
        if event:
            self.worker.publish("name.proposal", event)
            if event["state"] in {"confirmed", "rejected"}:
                self.worker.publish(
                    "hw.pattern",
                    {"name": "OK" if event["state"] == "confirmed" else "NO", "side": "R"},
                )

    def _tick(self) -> None:
        for event in self.names.expire(self.clock()):
            self.worker.publish("name.proposal", event)
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
                elif kind == "names" and source["speaker"]["track_id"] not in self.lost:
                    topic, event = "name.proposal", self.names.propose(source, answer, self.clock())
                    if event:
                        self.worker.publish("hw.pattern", {"name": "NAME", "side": "R"}, generation)
                elif kind == "replies":
                    topic, event = "reply.suggestions", replies.result(answer)
                elif kind == "descriptions" and source["track_id"] not in self.lost:
                    topic, event = (
                        "vision.description",
                        self.descriptions.result(source["track_id"], answer),
                    )
                if event:
                    self.worker.publish(topic, event, generation)
            except Exception:
                self.worker.error = "local language job failed"
                logger.exception("local language job failed")
        self.jobs = pending

    def stop(self) -> None:
        self.client.stop()
        self.worker.stop()
        if not self.worker.thread or not self.worker.thread.is_alive():
            self._clear()
