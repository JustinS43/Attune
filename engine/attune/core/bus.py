"""In-process publish/subscribe bus.

Section 4 - Pages, Engine & Demo. TODO: P-02. Contracts: docs/contracts.md (section 2).

`publish(topic, event)` calls every subscriber synchronously on the publisher's
thread; callbacks must return quickly (services hand work to their own threads).
A subscriber that raises is logged and skipped, so one broken section never stops
the others. Subscribing and unsubscribing are thread-safe and never block a publish
that is already running (subscriber lists are copied on write).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

Callback = Callable[[Any], None]
AnyCallback = Callable[[str, Any], None]


class Bus:
    """Thread-safe topic bus. `subscribe` returns an unsubscribe callable."""

    def __init__(self, error_log_every_s: float = 5.0) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, tuple[Callback, ...]] = {}
        self._any: tuple[AnyCallback, ...] = ()
        self._error_every = error_log_every_s
        self._error_t: dict[tuple[str, int], float] = {}
        self.errors = 0

    def subscribe(self, topic: str, callback: Callback) -> Callable[[], None]:
        """Call `callback(event)` for every event on `topic`."""
        with self._lock:
            self._subs[topic] = self._subs.get(topic, ()) + (callback,)

        def unsubscribe() -> None:
            with self._lock:
                subs = list(self._subs.get(topic, ()))
                if callback in subs:
                    subs.remove(callback)
                    self._subs[topic] = tuple(subs)

        return unsubscribe

    def subscribe_all(self, callback: AnyCallback) -> Callable[[], None]:
        """Call `callback(topic, event)` for every event (session log, debugging)."""
        with self._lock:
            self._any = self._any + (callback,)

        def unsubscribe() -> None:
            with self._lock:
                subs = list(self._any)
                if callback in subs:
                    subs.remove(callback)
                    self._any = tuple(subs)

        return unsubscribe

    def publish(self, topic: str, event: Any = None) -> None:
        """Deliver `event` to the subscribers of `topic` on this thread."""
        subs = self._subs.get(topic, ())
        for cb in subs:
            try:
                cb(event)
            except Exception:  # noqa: BLE001 - logged by _report
                self._report(topic, cb)
        for cb in self._any:
            try:
                cb(topic, event)
            except Exception:  # noqa: BLE001 - logged by _report
                self._report(topic, cb)

    def subscribers(self, topic: str) -> int:
        return len(self._subs.get(topic, ()))

    def _report(self, topic: str, cb: Callable) -> None:
        # Logged at most once per `error_log_every_s` per subscriber, so a callback that
        # fails on every 30/s frame cannot flood the log.
        self.errors += 1
        key = (topic, id(cb))
        now = time.monotonic()
        if now - self._error_t.get(key, -1e9) >= self._error_every:
            self._error_t[key] = now
            log.exception("Subscriber %r to %s failed", getattr(cb, "__qualname__", cb), topic)
