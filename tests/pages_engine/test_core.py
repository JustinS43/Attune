"""Bus, clock, config loader, contracts, status aggregation, session log and file player."""

from __future__ import annotations

import dataclasses
import json
import math
import threading
import time
import wave

import numpy as np
import pytest
from attune.config import load_config, merge
from attune.core import clock
from attune.core import contracts as C
from attune.core.bus import Bus
from attune.core.ringbuffer import TimedRing
from attune.core.session_log import SessionLog
from attune.core.status import StatusAggregator
from attune.replay.player import FilePlayer

from .conftest import wait_for

# ---------------------------------------------------------------- bus


def test_bus_delivers_in_order_and_unsubscribes():
    bus = Bus()
    got = []
    unsub = bus.subscribe("t", got.append)
    bus.publish("t", 1)
    bus.publish("t", 2)
    bus.publish("other", 99)
    unsub()
    bus.publish("t", 3)
    assert got == [1, 2]
    unsub()  # twice is harmless


def test_bus_isolates_subscriber_exceptions(caplog):
    bus = Bus()
    got = []

    def broken(ev):
        raise RuntimeError("boom")

    bus.subscribe("t", broken)
    bus.subscribe("t", got.append)
    for i in range(5):
        bus.publish("t", i)
    assert got == [0, 1, 2, 3, 4]
    assert bus.errors == 5
    # rate-limited: one log line, not five
    assert sum("boom" in (r.exc_text or "") or "failed" in r.message for r in caplog.records) == 1


def test_bus_subscribe_all_sees_every_topic():
    bus = Bus()
    seen = []
    unsub = bus.subscribe_all(lambda topic, ev: seen.append((topic, ev)))
    bus.publish("a", 1)
    bus.publish("b", 2)
    unsub()
    bus.publish("c", 3)
    assert seen == [("a", 1), ("b", 2)]


def test_bus_is_thread_safe_under_concurrent_subscribe():
    bus = Bus()
    counts = []
    stop = threading.Event()

    def churn():
        while not stop.is_set():
            bus.subscribe("t", lambda ev: None)()

    t = threading.Thread(target=churn)
    t.start()
    bus.subscribe("t", counts.append)
    for i in range(2000):
        bus.publish("t", i)
    stop.set()
    t.join()
    assert len(counts) == 2000


def test_bus_callback_may_unsubscribe_itself():
    bus = Bus()
    got = []
    holder = {}

    def once(ev):
        got.append(ev)
        holder["unsub"]()

    holder["unsub"] = bus.subscribe("t", once)
    bus.publish("t", 1)
    bus.publish("t", 2)
    assert got == [1]


# ---------------------------------------------------------------- clock and config


def test_clock_is_perf_counter_and_monotonic():
    a = clock.now()
    b = clock.now()
    assert b >= a
    assert abs(clock.now() - time.perf_counter()) < 0.01


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_config_merges_local_over_example(tmp_path):
    _write(
        tmp_path / "config" / "attune.example.toml",
        '[engine]\nport = 8000\nhost = "127.0.0.1"\n[pages]\njpeg_quality = 80\nbubble_chars = 42\n',
    )
    _write(tmp_path / "config" / "attune.toml", "[pages]\njpeg_quality = 60\n")
    cfg = load_config(cwd=tmp_path)
    assert cfg["pages"] == {"jpeg_quality": 60, "bubble_chars": 42}
    assert cfg["engine"]["port"] == 8000
    assert cfg["clock"] is clock.now


def test_config_example_only_and_explicit_path(tmp_path):
    _write(tmp_path / "config" / "attune.example.toml", "[engine]\nport = 8000\n")
    cfg = load_config(cwd=tmp_path)
    assert cfg["engine"]["port"] == 8000
    other = tmp_path / "other.toml"
    _write(other, "[engine]\nport = 9000\n")
    assert load_config(other, cwd=tmp_path)["engine"]["port"] == 9000
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.toml", cwd=tmp_path)


def test_config_repo_example_loads():
    cfg = load_config(cwd="/")  # falls back to the repo's config/attune.example.toml
    for table in ("engine", "vision", "audio", "pages", "speech_out"):
        assert table in cfg
    assert callable(cfg["clock"])


def test_merge_is_deep_and_does_not_mutate():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    out = merge(base, {"a": {"y": 3}, "c": 4})
    assert out == {"a": {"x": 1, "y": 3}, "b": 1, "c": 4}
    assert base == {"a": {"x": 1, "y": 2}, "b": 1}


# ---------------------------------------------------------------- contracts


def test_contracts_match_vision_types():
    """Section 1's local types and core.contracts must keep the same field names."""
    T = pytest.importorskip("attune.vision.types")
    for name in (
        "Track",
        "Speaker",
        "FaceState",
        "Offscreen",
        "Scene",
        "Caption",
        "EnrollResult",
        "PersonChanged",
        "StatusPart",
        "VoiceHarvest",
        "TrackLost",
        "Tracks",
        "Frame",
        "Appearance",
    ):
        ours = [f.name for f in dataclasses.fields(getattr(C, name))]
        theirs = [f.name for f in dataclasses.fields(getattr(T, name))]
        assert ours == theirs, name
    for topic in (
        "VISION_FRAME",
        "SCENE",
        "CAPTION",
        "STATUS_PART",
        "COMMAND",
        "PAUSED",
    ):
        assert getattr(C, topic) == getattr(T, topic)


def test_contract_names():
    assert "pause.toggle" in C.COMMAND_NAMES and "mark" in C.COMMAND_NAMES
    assert len(C.COMMAND_NAMES) == 13
    assert C.WS_AUDIENCE[C.WS_STATUS] == {"console"}
    assert C.WS_AUDIENCE[C.WS_CAPTION] == {"lens", "console", "phone"}
    assert C.EnrollResult(None, "face", False).reason == ""


# ---------------------------------------------------------------- ring buffer and status


def test_timed_ring_window():
    ring = TimedRing(1.0)
    ring.add(0.0, 1)
    ring.add(0.5, 3)
    assert ring.mean(0.6) == 2
    ring.add(1.2, 5)
    assert ring.values(1.2) == [3, 5]
    assert ring.rate(1.2) == 2.0


def test_status_aggregates_parts_and_levels():
    bus = Bus()
    now = [100.0]
    agg = StatusAggregator(bus, clock=lambda: now[0])
    agg.connect()
    out = []
    bus.subscribe(C.STATUS, out.append)
    bus.publish(C.STATUS_PART, C.StatusPart("vision", True, "", {"vision_fps": 29.5}))
    bus.publish(C.STATUS_PART, {"part": "llm", "ok": False, "detail": "warming", "metrics": {}})
    bus.publish(C.HW_LINK, {"connected": True, "firmware": "1.0", "driver": "TB6612"})
    t = np.arange(16000) / 16000
    sine = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    for i in range(0, 16000, 160):
        bus.publish(
            C.AUDIO_BLOCK,
            {"t": now[0], "sample_rate": 16000, "samples": sine[i : i + 160]},
        )
    bus.publish(C.AUDIO_BLOCK, {"t": now[0], "sample_rate": 32000, "samples": np.ones(320)})
    bus.publish(
        C.CAPTION,
        {
            "utt_id": "u1",
            "final": True,
            "words": [("hi", 99.0, 99.2), ("there", 99.3, 99.5)],
        },
    )
    snap = agg.snapshot()
    assert snap["fps"] == 29.5
    assert snap["arduino"] == {"connected": True, "firmware": "1.0", "driver": "TB6612"}
    assert snap["ollama"] == {"ok": False, "detail": "warming", "warm": None}
    assert snap["mic_level"] == pytest.approx(20 * math.log10(0.1 / math.sqrt(2)), abs=0.3)
    assert snap["caption_delay"] == pytest.approx(0.5, abs=1e-6)
    assert snap["parts"]["vision"]["ok"] is True and snap["parts"]["llm"]["ok"] is False
    assert snap["on_battery"] in (True, False, None)
    now[0] += 10
    assert agg.snapshot()["parts"]["vision"]["stale"] is True
    json.dumps(snap)  # JSON-safe


def test_status_publishes_every_period():
    bus = Bus()
    out = []
    bus.subscribe(C.STATUS, out.append)
    agg = StatusAggregator(bus, period_s=0.05, gpu_probe_s=1e9)
    agg.start()
    assert wait_for(lambda: len(out) >= 2)
    agg.stop()


# ---------------------------------------------------------------- session log


def test_session_log_is_privacy_safe(tmp_path):
    bus = Bus()
    log = SessionLog(bus, tmp_path, "s1")
    log.start()
    speaker = C.Speaker("face", 3, "sam-1", "Sam", "left")
    bus.publish(C.CAPTION, C.Caption("u1", speaker, "secret words here", False, "en", []))
    bus.publish(C.CAPTION, C.Caption("u1", speaker, "secret words here", True, "en", []))
    bus.publish(
        C.CAPTION_TRANSLATION,
        {"utt_id": "u1", "source_lang": "es", "text_en": "hidden"},
    )
    bus.publish(
        C.NAME_PROPOSAL,
        {"proposal_id": "p1", "track_id": 3, "name": "Samantha", "state": "proposed"},
    )
    bus.publish(C.COMMAND, {"name": "speak", "args": {"text": "typed private text"}})
    bus.publish(C.VISION_FRAME, {"frame_no": 1, "t": 0.0, "image": np.zeros((4, 4, 3))})
    bus.publish(C.STATUS_PART, {"part": "vision", "ok": True})
    bus.publish(C.STATUS_PART, {"part": "vision", "ok": True})
    bus.publish(C.STATUS_PART, {"part": "vision", "ok": False})
    log.mark("demo starts")
    log.stop()
    text = (tmp_path / "s1.jsonl").read_text(encoding="utf-8")
    for secret in ("secret", "hidden", "Samantha", "Sam", "typed private"):
        assert secret not in text
    rows = [json.loads(line) for line in text.splitlines()]
    topics = [r["topic"] for r in rows]
    assert topics[0] == "session.start" and topics[-1] == "session.end"
    assert topics.count("caption") == 1  # the final only
    assert topics.count("status.part") == 2  # changes only
    assert "vision.frame" not in topics
    assert {"topic": "command", "name": "speak"}.items() <= next(
        r for r in rows if r["topic"] == "command"
    ).items()
    assert any(r["topic"] == "mark" and r["note"] == "demo starts" for r in rows)


# ---------------------------------------------------------------- file player


def test_file_player_publishes_both_rates_on_the_clock(tmp_path):
    path = tmp_path / "tone.wav"
    samples = (0.2 * np.sin(np.arange(9600) / 10) * 32767).astype(np.int16)  # 0.2 s at 48 kHz
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(samples.tobytes())
    bus = Bus()
    blocks = []
    bus.subscribe(C.AUDIO_BLOCK, blocks.append)
    player = FilePlayer(bus, str(path), clock.now, delay_s=0.0)
    t0 = clock.now()
    player.start()
    assert wait_for(lambda: player.plays == 1, timeout=3)
    elapsed = clock.now() - t0
    player.stop()
    by_rate = {r: [b for b in blocks if b["sample_rate"] == r] for r in (16000, 32000)}
    assert sum(len(b["samples"]) for b in by_rate[16000]) == pytest.approx(3200, abs=16)
    assert sum(len(b["samples"]) for b in by_rate[32000]) == pytest.approx(6400, abs=32)
    ts = [b["t"] for b in by_rate[16000]]
    assert ts == sorted(ts) and ts[1] - ts[0] == pytest.approx(0.01)
    assert by_rate[16000][0]["samples"].dtype == np.float32
    assert elapsed >= 0.15  # real time, not as fast as possible
