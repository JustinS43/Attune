"""Privacy-safe session log: what happened and when, never what was said.

Section 4 - Pages, Engine & Demo. TODO: P-13 (log part).

Writes one JSON line per notable bus event to `data/sessions/<session_id>.jsonl`
(a new file per engine run). Only event types, times and a few non-identifying
fields are kept: never caption text, translations, names, typed replies, command
arguments, audio, frames or crops (history is Section 3's job). `mark` notes from
the console are the exception: the operator types them for the log.

The file is written by its own thread, so bus publishers never wait on the disk.
"""

from __future__ import annotations

import datetime
import json
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contracts import get

log = logging.getLogger(__name__)


def new_session_id() -> str:
    """Sortable, unique id: local time plus a short random suffix."""
    return datetime.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def _caption(ev: Any) -> dict | None:
    if not get(ev, "final", False):
        return None  # drafts are too frequent to log
    return {
        "utt_id": get(ev, "utt_id"),
        "lang": get(ev, "lang"),
        "speaker": get(get(ev, "speaker"), "kind"),
        "words": len(get(ev, "words") or []),
    }


def _command(ev: Any) -> dict | None:
    name = get(ev, "name")
    if name == "mark":  # logged separately by `mark()`
        return None
    return {"name": name}


def _fields(*names: str) -> Callable[[Any], dict]:
    return lambda ev: {n: get(ev, n) for n in names}


# topic -> extractor of the safe fields (None from an extractor skips the event)
LOGGED: dict[str, Callable[[Any], dict | None]] = {
    "caption": _caption,
    "caption.translation": _fields("utt_id", "source_lang"),
    "alert": _fields("alert_id", "kind", "side", "confidence", "state"),
    "name.proposal": _fields("proposal_id", "track_id", "state"),
    "enroll.result": _fields("part", "ok", "reason"),
    "person.changed": _fields("action"),
    "paused": _fields("paused"),
    "session.forget": lambda ev: {},
    "command": _command,
    "hw.link": _fields("connected", "driver", "firmware"),
    "hw.pattern": _fields("name", "side"),
    "touch.action": _fields("target", "accept"),
    "sensors.touch": _fields("gesture"),
    "speech_out.playing": _fields("state"),
    "reply.spoken": _fields("voice"),
    "reply.suggestions": lambda ev: {"count": len(get(ev, "options") or [])},
    "vision.track_lost": _fields("track_id", "side"),
}


class SessionLog:
    """JSONL log of notable bus events for one engine session."""

    def __init__(
        self,
        bus,
        root: str | Path,
        session_id: str | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.bus, self.clock = bus, clock
        self.session_id = session_id or new_session_id()
        self.path = Path(root) / f"{self.session_id}.jsonl"
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._unsub: Callable[[], None] | None = None
        self._status: dict[str, bool] = {}
        self.lines = 0

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._unsub = self.bus.subscribe_all(self.on_event)
        self._thread = threading.Thread(target=self._run, name="session-log", daemon=True)
        self._thread.start()
        self.write("session.start", {"session_id": self.session_id})
        log.info("Session log: %s", self.path)

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        self.write("session.end", {})
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2.0)

    def on_event(self, topic: str, ev: Any) -> None:
        if topic == "status.part":  # only log health changes, not the 1/s heartbeat
            part, ok = get(ev, "part"), bool(get(ev, "ok", True))
            if self._status.get(part) != ok:
                self._status[part] = ok
                self.write(topic, {"part": part, "ok": ok})
            return
        extract = LOGGED.get(topic)
        if extract is None:
            return
        try:
            fields = extract(ev)
        except Exception:  # noqa: BLE001 - a malformed event must not break the bus
            fields = {"malformed": True}
        if fields is not None:
            self.write(topic, fields)

    def mark(self, note: str) -> None:
        """An operator note from the console's Mark button."""
        self.write("mark", {"note": str(note)[:500]})

    def write(self, topic: str, fields: dict) -> None:
        wall = datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._queue.put({"t": round(self.clock(), 3), "wall": wall, "topic": topic, **fields})

    def _run(self) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                while True:
                    item = self._queue.get()
                    if item is None:
                        return
                    fh.write(json.dumps(item, default=str) + "\n")
                    self.lines += 1
                    if self._queue.empty():
                        fh.flush()
        except OSError:
            log.exception("Session log %s failed; continuing without it", self.path)
