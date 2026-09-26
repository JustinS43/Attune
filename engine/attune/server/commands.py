"""Page commands: validate, publish on the bus, and handle the engine's own.

Section 4 - Pages, Engine & Demo. TODO: P-04. Contracts: docs/contracts.md (4).

Every valid `{"type": "command", name, args}` from a page is published as the bus
event `command` = {name, args}; the owning section picks it up. The engine itself
also handles three of them:

- `pause.toggle`: flips the pause state and publishes `paused` {paused}.
  A touch-sensor `touch.action` with target `pause` does the same.
- `session.forget`: publishes `session.forget` {} so every section wipes session data.
- `mark`: writes the note to the session log.

Unknown command names are logged and ignored.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from ..core.contracts import COMMAND, COMMAND_NAMES, PAUSED, SESSION_FORGET, TOUCH_ACTION, get

log = logging.getLogger(__name__)


class CommandRouter:
    """Routes page commands to the bus; owns the engine's pause state."""

    def __init__(self, bus, session_log=None) -> None:
        self.bus = bus
        self.session_log = session_log
        self._lock = threading.Lock()
        self.paused = False
        self.handled = 0
        self.ignored = 0
        self._unsubs: list[Callable[[], None]] = []

    def connect(self) -> None:
        """Follow `paused` from any publisher and handle the touch sensor's pause."""
        self._unsubs = [
            self.bus.subscribe(PAUSED, self._on_paused),
            self.bus.subscribe(TOUCH_ACTION, self._on_touch),
        ]

    def close(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    def _on_paused(self, ev: Any) -> None:
        with self._lock:
            self.paused = bool(get(ev, "paused", False))

    def _on_touch(self, ev: Any) -> None:
        if get(ev, "target") == "pause":
            self.toggle_pause()

    def toggle_pause(self) -> bool:
        with self._lock:
            paused = not self.paused
        self.set_paused(paused)
        return paused

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self.paused = bool(paused)
        log.info("Recognition %s", "paused" if paused else "resumed")
        self.bus.publish(PAUSED, {"paused": bool(paused)})

    def handle(self, name: Any, args: Any = None) -> bool:
        """Handle one page command; returns False (and logs) when it is not in the contract."""
        if not isinstance(name, str) or name not in COMMAND_NAMES:
            self.ignored += 1
            log.info("Ignoring unknown command %r", name)
            return False
        if not isinstance(args, dict):
            args = {}
        self.handled += 1
        if name == "pause.toggle":
            self.toggle_pause()
        elif name == "session.forget":
            log.info("Forgetting the session")
            self.bus.publish(SESSION_FORGET, {})
        elif name == "mark":
            note = args.get("note", "")
            if self.session_log is not None:
                self.session_log.mark(note if isinstance(note, str) else str(note))
        self.bus.publish(COMMAND, {"name": name, "args": args})
        return True
