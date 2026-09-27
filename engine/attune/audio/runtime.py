"""Section-local adapters until the shared clock and typed events land."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

logger = logging.getLogger(__name__)


def fields(event: Any) -> dict:
    """Accept contract dictionaries or the forthcoming shared dataclasses."""
    if isinstance(event, Mapping):
        return dict(event)
    if is_dataclass(event):
        return asdict(event)
    return vars(event)


def engine_clock(config: dict) -> Callable[[], float]:
    """Require a shared clock; never create a service-specific time origin."""
    if callable(config.get("clock")):
        return config["clock"]
    from attune.core import clock

    if callable(getattr(clock, "now", None)):
        return clock.now
    raise RuntimeError("P-02 required: supply config['clock'] = shared monotonic clock")


class Worker:
    """Bounded, nonblocking bus inbox with health and stale-result invalidation."""

    def __init__(
        self,
        bus: Any,
        part: str,
        handler: Callable,
        tick: Callable | None = None,
        maxsize: int = 256,
    ):
        self.bus, self.part, self.handler, self.tick = bus, part, handler, tick
        self.inbox: queue.Queue = queue.Queue(maxsize=maxsize)
        self.controls: queue.SimpleQueue = queue.SimpleQueue()
        self.cleanup: Callable | None = None
        self.health: Callable[[], dict] | None = None
        self.closed = threading.Event()
        self.thread: threading.Thread | None = None
        self.generation = 0
        self.dropped = 0
        self.error = ""
        self._subscriptions: list = []
        self._lock = threading.RLock()

    def subscribe(self, topic: str, accept: Callable[[Any], bool] | None = None) -> None:
        """Register a fast callback, retaining an unsubscribe hook when supported.

        `accept` filters events on the publisher's thread, before they take inbox room.
        """

        def receive(event: Any) -> None:
            if self.closed.is_set() or (accept is not None and not accept(event)):
                return
            # enroll.result is rare and must never be lost to an audio backlog: it starts the
            # voice step of an enrollment the person consented to (P-29)
            control = topic in {
                "session.forget",
                "paused",
                "person.changed",
                "speech_out.playing",
                "command",
                "touch.action",
                "enroll.result",
            }
            invalidates = topic in {
                "session.forget",
                "paused",
                "person.changed",
                "speech_out.playing",
            }
            if topic == "command":
                invalidates = fields(event).get("name") in {"person.delete", "languages.set"}
            elif topic == "person.changed":
                # someone new being saved makes nothing in flight wrong; a rename or delete does.
                # Vision sends "enrolled" right after the face result, which must survive it.
                invalidates = fields(event).get("action") != "enrolled"
            if invalidates:
                with self._lock:
                    self.generation += 1
            target = self.controls if control else self.inbox
            try:
                target.put_nowait((topic, event, self.generation))
            except queue.Full:
                self.dropped += 1
                self.error = "audio/event backlog; discontinuous recognition is discarded"

        self._subscriptions.append(self.bus.subscribe(topic, receive))

    def start(self) -> None:
        """Run the inbox on a dedicated daemon thread."""
        if self.thread and self.thread.is_alive():
            return
        self.closed.clear()
        self.thread = threading.Thread(target=self._run, name=self.part, daemon=True)
        self.thread.start()

    def publish(self, topic: str, event: dict, generation: int | None = None) -> None:
        """Suppress output after shutdown or session invalidation."""
        with self._lock:
            if not self.closed.is_set() and (generation is None or generation == self.generation):
                self.bus.publish(topic, event)

    def _run(self) -> None:
        health = 0.0
        while not self.closed.is_set():
            try:
                try:
                    topic, event, generation = self.controls.get_nowait()
                    control = True
                except queue.Empty:
                    topic, event, generation = self.inbox.get(timeout=0.05)
                    control = False
                if (
                    control
                    or generation == self.generation
                    or topic
                    in {
                        "session.forget",
                        "paused",
                        "person.changed",
                        "speech_out.playing",
                    }
                ):
                    self.handler(topic, fields(event), generation)
            except queue.Empty:
                pass
            except Exception:
                self.error = "worker operation failed; see local log"
                logger.exception("%s operation failed", self.part)
            try:
                if self.tick:
                    self.tick()
            except Exception:
                self.error = "periodic operation failed"
                logger.exception("%s tick failed", self.part)
            if time.monotonic() - health >= 1:
                health = time.monotonic()
                capture = self.health() if self.health else {}
                self.publish(
                    "status.part",
                    {
                        "part": self.part,
                        "ok": not self.error and capture.get("ok", True),
                        "detail": self.error or capture.get("detail", "running"),
                        "metrics": {"dropped": self.dropped, **capture.get("metrics", {})},
                    },
                )

        if self.cleanup:
            self.cleanup()

    def stop(self) -> None:
        """Cancel publications immediately and wait at most 1.5 seconds."""
        self.closed.set()
        self.generation += 1
        for unsubscribe in self._subscriptions:
            if callable(unsubscribe):
                unsubscribe()
        if self.thread:
            self.thread.join(timeout=1.5)
        while True:
            try:
                self.inbox.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                self.controls.get_nowait()
            except queue.Empty:
                break
