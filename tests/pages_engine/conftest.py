"""Fixtures for Section 4 tests: a real bus, hub and FastAPI app, no devices or models."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ENGINE = Path(__file__).resolve().parents[2] / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from attune.core import contracts as C
from attune.core.bus import Bus
from attune.core.session_log import SessionLog
from attune.server.app import create_app
from attune.server.commands import CommandRouter
from attune.server.ws import Hub

CONFIG = {
    "engine": {"data_dir": "data"},
    "vision": {"width": 1920, "height": 1080},
    "pages": {
        "frame_width": 1280,
        "frame_height": 720,
        "jpeg_quality": 70,
        "bubble_chars": 42,
        "bubble_lines": 2,
        "bubble_fade_s": 4,
        "thumbnail_every_s": 0.2,
    },
    "speech_out": {"presets": ["Nice to meet you", "One moment"]},
}


def wait_for(predicate, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class Recorder:
    """Collects bus events per topic."""

    def __init__(self, bus: Bus, *topics: str) -> None:
        self.events: dict[str, list] = {t: [] for t in topics}
        for t in topics:
            bus.subscribe(t, self.events[t].append)

    def __getitem__(self, topic: str) -> list:
        return self.events[topic]


def recv(ws, timeout: float = 5.0) -> dict | bytes:
    """Next message from a TestClient WebSocket, as a dict (JSON) or bytes (frame)."""
    import json

    import anyio

    async def _receive():
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    message = ws.portal.call(_receive)
    if message["type"] == "websocket.close":
        raise ConnectionError("closed")
    if message.get("text") is not None:
        return json.loads(message["text"])
    return message["bytes"]


def recv_type(ws, msg_type: str, timeout: float = 5.0, seen: list | None = None) -> dict:
    """Skip messages until one of `msg_type` arrives (frames and others go to `seen`)."""
    end = time.monotonic() + timeout
    while True:
        left = end - time.monotonic()
        if left <= 0:
            raise TimeoutError(msg_type)
        msg = recv(ws, left)
        if isinstance(msg, dict) and msg.get("type") == msg_type:
            return msg
        if seen is not None:
            seen.append(msg)


@pytest.fixture
def hub_env(tmp_path):
    from fastapi.testclient import TestClient

    data = tmp_path / "data"
    (data / "reels" / "film").mkdir(parents=True)
    (data / "reels" / "film" / "timeline.json").write_text('{"ok": true}')
    person = data / "people" / "sam-abc123"
    person.mkdir(parents=True)
    (person / "meta.json").write_text('{"name": "Sam", "consent_t": "2026-09-26T10:00:00-04:00"}')
    (person / "face.npy").write_bytes(b"not really a numpy file")

    bus = Bus()
    rec = Recorder(bus, C.COMMAND, C.PAUSED, C.SESSION_FORGET)
    session_log = SessionLog(bus, data / "sessions", "test-session")
    router = CommandRouter(bus, session_log)
    router.connect()
    hub = Hub(bus, CONFIG, "test-session", router, data_dir=data)
    hub.connect()
    hub.start()
    app = create_app(hub, data_root=tmp_path)
    with TestClient(app) as client:
        yield SimpleNamespace(
            bus=bus,
            hub=hub,
            client=client,
            rec=rec,
            router=router,
            data=data,
            log=session_log,
        )
    hub.stop()
