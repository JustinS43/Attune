"""main.py: argument parsing, service start-up with failures, a real uvicorn server, shutdown."""

from __future__ import annotations

import json
import time

import pytest
from attune import main as M
from attune.core import contracts as C


def test_parse_args():
    o = M.parse_args(
        ["--source", "2", "--audio-file", "x.wav", "--port", "8001", "--no-browser"]
    )
    assert o.source == 2 and o.no_mic is True and o.port == 8001 and o.no_browser
    o = M.parse_args(
        ["--source", "data/reels/film/cafe_friends.mp4", "--repeat-audio", "4"]
    )
    assert o.source == "data/reels/film/cafe_friends.mp4" and o.repeat_audio == 4.0
    assert o.no_mic is False and o.simulate_hardware is False
    o = M.parse_args(["--simulate-hardware", "--no-mic"])
    assert o.simulate_hardware and o.no_mic
    engine = M.Engine(o, {"engine": {"data_dir": "data"}, "hardware": {"baud": 115200}})
    assert engine.config["hardware"] == {"baud": 115200, "simulate": True}


class Good:
    started = stopped = 0

    def __init__(self, bus, config):
        self.bus = bus

    def start(self):
        Good.started += 1
        self.bus.publish(
            C.STATUS_PART, {"part": "good", "ok": True, "detail": "", "metrics": {}}
        )

    def stop(self):
        Good.stopped += 1


class Broken(Good):
    def start(self):
        raise RuntimeError("no device")


class Slow(Good):
    def stop(self):
        time.sleep(10)  # must not hold up shutdown past the budget


def test_engine_skips_failures_serves_pages_and_stops_quickly(tmp_path, monkeypatch):
    websockets_sync = pytest.importorskip("websockets.sync.client")
    monkeypatch.chdir(tmp_path)
    config = {
        "engine": {"host": "127.0.0.1", "port": 0, "data_dir": str(tmp_path / "data")},
        "pages": {},
        "speech_out": {"presets": ["One moment"]},
    }
    engine = M.Engine(M.Options(port=0, no_browser=True), config)
    steps = [
        ("good", lambda: Good(engine.bus, config)),
        ("broken", lambda: Broken(engine.bus, config)),
        ("missing", lambda: None),
        ("slow", lambda: Slow(engine.bus, config)),
    ]
    monkeypatch.setattr(engine, "_steps", lambda: steps)
    engine.start()
    try:
        assert set(engine.failed) == {"broken", "missing"}
        assert [n for n, _ in engine.running] == ["good", "slow", "server"]
        port = engine.web.server.servers[0].sockets[0].getsockname()[1]
        with websockets_sync.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            ws.send(json.dumps({"type": "hello", "role": "console", "frames": False}))
            welcome = json.loads(ws.recv(timeout=5))
            assert welcome["type"] == "welcome" and welcome["seq"] == 1
            assert welcome["session_id"] == engine.session_id
            assert welcome["config"]["presets"] == ["One moment"]
            ws.send(json.dumps({"type": "command", "name": "pause.toggle", "args": {}}))
            deadline = time.monotonic() + 5
            status = paused = None
            while time.monotonic() < deadline and (status is None or paused is None):
                msg = json.loads(ws.recv(timeout=5))
                if msg["type"] == "paused":
                    paused = msg
                elif msg["type"] == "status":
                    status = msg
            assert paused["paused"] is True
            assert status["parts"]["broken"]["ok"] is False
            assert "no device" in status["parts"]["broken"]["detail"]
            assert status["parts"]["good"]["ok"] is True
    finally:
        t0 = time.monotonic()
        engine.stop()
        took = time.monotonic() - t0
    assert took < 3.6
    assert Good.stopped >= 1
    logs = list((tmp_path / "data" / "sessions").glob("*.jsonl"))
    assert len(logs) == 1 and "pause.toggle" in logs[0].read_text(encoding="utf-8")
