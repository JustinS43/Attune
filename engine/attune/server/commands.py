"""Page commands: validate, publish on the bus, and handle the engine's own.

Section 4 - Pages, Engine & Demo. TODO: P-04. Contracts: docs/contracts.md (4).

Every valid `{"type": "command", name, args}` from a page is published as the bus
event `command` = {name, args}; the owning section picks it up there. Unknown
command names are logged and ignored.

The engine owns three commands and handles them from the bus `command` topic, so
they work the same whoever sends them (a page, or Section 3's touch router, which
publishes `command` pause.toggle on a double tap):

- `pause.toggle`: flips the pause state and publishes `paused` {paused}.
- `session.forget`: publishes `session.forget` {} so every section wipes session data.
- `mark`: writes the note to the session log.

Handling never publishes `command` again, so there is no loop.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from ..core.contracts import COMMAND, COMMAND_NAMES, PAUSED, SESSION_FORGET, get

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
        """Handle engine-owned commands from the bus and follow `paused` from anyone."""
        if self._unsubs:
            return
        self._unsubs = [
            self.bus.subscribe(COMMAND, self._on_command),
            self.bus.subscribe(PAUSED, self._on_paused),
        ]

    def close(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    # ---- from the pages ----
    def handle(self, name: Any, args: Any = None) -> bool:
        """Publish one page command; returns False (and logs) when it is not in the contract."""
        if not isinstance(name, str) or name not in COMMAND_NAMES:
            self.ignored += 1
            log.info("Ignoring unknown command %r", name)
            return False
        if not isinstance(args, dict):
            args = {}
        self.handled += 1
        self.bus.publish(COMMAND, {"name": name, "args": args})
        return True

    # ---- from the bus ----
    def _on_command(self, ev: Any) -> None:
        name = get(ev, "name")
        args = get(ev, "args") or {}
        if name == "pause.toggle":
            self.toggle_pause()
        elif name == "session.forget":
            log.info("Forgetting the session")
            self.bus.publish(SESSION_FORGET, {})
        elif name == "mark" and self.session_log is not None:
            note = args.get("note", "") if isinstance(args, dict) else ""
            self.session_log.mark(note if isinstance(note, str) else str(note))

    def _on_paused(self, ev: Any) -> None:
        with self._lock:
            self.paused = bool(get(ev, "paused", False))

    def toggle_pause(self) -> bool:
        with self._lock:
            paused = not self.paused
            self.paused = paused
        log.info("Recognition %s", "paused" if paused else "resumed")
        self.bus.publish(PAUSED, {"paused": paused})
        return paused
