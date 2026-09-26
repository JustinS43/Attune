"""HardwareService + SerialLink against the in-process FakeArduino (no rig needed)."""

import time
from types import SimpleNamespace

import pytest
from attune.hardware import serial_link
from attune.hardware.serial_link import ClockMap, rank_port
from attune.hardware.service import HardwareService
from attune.hardware.simulator import FakeArduino
from conftest import wait_for

CFG = {
    "ready_timeout_s": 1.0,
    "heartbeat_s": 0.1,
    "report_ms": 50,
    "tap_ms": 350,
    "hold_ms": 700,
    "led_brightness": 120,
    "stale_s": 1.0,
    "scan_s": 0.1,
}


@pytest.fixture
def rig(bus, monkeypatch):
    monkeypatch.delenv("ATTUNE_SIMULATE_HARDWARE", raising=False)
    fakes = []

    def connect():
        fake = FakeArduino(seed=1, heartbeat_timeout_s=0.5)
        fakes.append(fake)
        return fake

    service = HardwareService(
        bus, {"clock": time.monotonic, "hardware": dict(CFG)}, connect=connect
    )
    service.start()
    assert wait_for(lambda: service.link.connected)
    yield SimpleNamespace(service=service, bus=bus, fakes=fakes, fake=lambda: fakes[-1])
    service.stop()


def test_ready_publishes_link_and_sends_cfg(rig):
    links = rig.bus.of("hw.link")
    assert links[0] == {"connected": False, "firmware": None, "driver": None}
    assert links[-1] == {"connected": True, "firmware": "1.0.0-sim", "driver": "TB6612"}
    fake = rig.fake()
    assert wait_for(
        lambda: fake.cfg == {"rate": 50, "tap_ms": 350, "hold_ms": 700, "led": 120}
    )
    assert wait_for(lambda: fake.icon == "HEART")


def test_levels_flow_on_shared_clock(rig):
    assert wait_for(lambda: len(rig.bus.of("sensors.levels")) >= 5)
    levels = rig.bus.of("sensors.levels")
    e = levels[-1]
    assert set(e) == {"t", "left", "right", "motor_on"}
    assert 0 <= e["left"] <= 1023 and 0 <= e["right"] <= 1023
    assert abs(e["t"] - time.monotonic()) < 0.5
    ts = [x["t"] for x in levels]
    assert ts == sorted(ts)


def test_touch_acknowledges_alert(rig):
    rig.bus.publish(
        "alert",
        {
            "alert_id": "a1",
            "kind": "smoke",
            "side": "left",
            "confidence": 0.9,
            "state": "start",
        },
    )
    assert wait_for(lambda: rig.fake().icon == "ALERT_L")
    rig.bus.publish("hw.sim_touch", {"gesture": "tap"})
    assert wait_for(lambda: rig.bus.of("touch.action"))
    assert rig.bus.of("sensors.touch")[-1]["gesture"] == "tap"
    assert rig.bus.of("touch.action")[-1] == {
        "target": "alert",
        "id": "a1",
        "accept": True,
    }
    assert wait_for(lambda: rig.fake().icon == "HEART")


def test_double_tap_asks_to_save(rig):
    assert rig.service.simulate_touch("double")
    assert wait_for(lambda: rig.bus.of("touch.action"))
    assert rig.bus.of("sensors.touch")[-1]["gesture"] == "double"
    assert rig.bus.of("touch.action")[-1] == {
        "target": "save",
        "id": None,
        "accept": True,
    }
    assert not rig.bus.of("command")  # a double tap no longer pauses


def test_triple_tap_pause_and_icon(rig):
    rig.bus.publish("hw.sim_touch", {"gesture": "triple"})
    assert wait_for(lambda: rig.bus.of("command"))
    assert rig.bus.of("command")[-1] == {"name": "pause.toggle", "args": {}}
    rig.bus.publish("paused", {"paused": True})
    assert wait_for(lambda: rig.fake().icon == "PAUSE")
    rig.bus.publish("paused", {"paused": False})
    assert wait_for(lambda: rig.fake().icon == "HEART")


def test_pattern_is_sent_and_acked(rig):
    rig.bus.publish("hw.pattern", {"name": "T3", "side": "right"})
    fake = rig.fake()
    assert wait_for(lambda: any(line.startswith("PAT") for line in fake.received))
    pat = next(line for line in fake.received if line.startswith("PAT"))
    assert pat.split()[2:] == ["R", "T3"]
    assert wait_for(lambda: rig.service.link.metrics["last_ack_ms"] is not None)
    assert wait_for(lambda: fake.active_pattern == "T3")
    assert wait_for(
        lambda: any(e["motor_on"] for e in rig.bus.of("sensors.levels")), 2.0
    )
    rig.bus.publish("hw.stop", {})
    assert wait_for(lambda: fake.active_pattern is None)


def test_pattern_test_command(rig):
    rig.bus.publish(
        "command", {"name": "pattern.test", "args": {"name": "BELL", "side": "L"}}
    )
    assert wait_for(
        lambda: any(line.endswith("L BELL") for line in rig.fake().received)
    )


def test_unknown_pattern_ignored(rig):
    rig.bus.publish("hw.pattern", {"name": "SIREN", "side": "L"})
    time.sleep(0.2)
    assert not any(line.startswith("PAT") for line in rig.fake().received)


def test_heartbeat_loss_stops_everything(rig):
    fake = rig.fake()
    rig.bus.publish("hw.pattern", {"name": "T3", "side": "L"})
    assert wait_for(lambda: fake.active_pattern == "T3")
    rig.service.link.send_heartbeat = False
    assert wait_for(lambda: fake.icon == "LOST", timeout=2.0)
    assert fake.active_pattern == "LOST" or fake.active_pattern is None
    assert fake.background is None and fake.foreground is None
    rig.service.link.send_heartbeat = True
    assert wait_for(lambda: fake.icon == "HEART", timeout=2.0)


def test_unplug_reconnects(rig):
    first = rig.fake()
    first.unplug()
    assert wait_for(
        lambda: rig.bus.of("hw.link")[-1]["connected"] is False, timeout=3.0
    )
    assert wait_for(
        lambda: len(rig.fakes) >= 2 and rig.service.link.connected, timeout=3.0
    )
    assert rig.bus.of("hw.link")[-1]["connected"] is True


def test_no_device_reports_disconnected_and_keeps_scanning(bus):
    calls = []

    def connect():
        calls.append(1)

    service = HardwareService(
        bus, {"clock": time.monotonic, "hardware": dict(CFG)}, connect=connect
    )
    service.start()
    try:
        assert wait_for(lambda: len(calls) >= 3)
        assert bus.of("hw.link") == [
            {"connected": False, "firmware": None, "driver": None}
        ]
        bus.publish("hw.pattern", {"name": "T3", "side": "L"})  # dropped, no crash
        assert wait_for(lambda: bus.of("status.part"), timeout=2.0)
        status = bus.of("status.part")[-1]
        assert status["part"] == "hardware" and status["ok"] is False
    finally:
        service.stop()


class SilentBoard:
    """Already-running board: streams LV but does not print READY again."""

    name = "silent"

    def __init__(self):
        self.t = 0
        self.written = []

    def write_line(self, line):
        self.written.append(line)

    def read_line(self, timeout):
        time.sleep(0.01)
        self.t += 50
        return f"LV {self.t} 20 30 0"

    def close(self):
        pass


def test_board_without_ready_still_links(bus):
    board = SilentBoard()
    service = HardwareService(
        bus,
        {"clock": time.monotonic, "hardware": dict(CFG, ready_timeout_s=0.3)},
        connect=lambda: board,
    )
    service.start()
    try:
        assert wait_for(lambda: service.link.connected, timeout=2.0)
        assert bus.of("hw.link")[-1] == {
            "connected": True,
            "firmware": "?",
            "driver": "?",
        }
        assert "HB" in board.written
    finally:
        service.stop()


def test_simulate_via_env(bus, monkeypatch):
    monkeypatch.setenv("ATTUNE_SIMULATE_HARDWARE", "1")
    service = HardwareService(bus, {"clock": time.monotonic, "hardware": dict(CFG)})
    assert service.simulate
    service.start()
    try:
        assert wait_for(lambda: service.link.connected)
        assert wait_for(lambda: bus.of("sensors.levels"))
    finally:
        service.stop()


def test_rank_port_prefers_arduino():
    assert rank_port(0x2341, 0x1002) == 0
    assert rank_port(0x2341, 0x0043) == 1
    assert rank_port(0x1A86, 0x7523) > 1
    assert rank_port(0x046D, 0x0825) is None  # a webcam
    assert rank_port(None, None) is None


def test_find_port(monkeypatch):
    ports = [
        SimpleNamespace(device="COM3", vid=0x1A86, pid=0x7523),
        SimpleNamespace(device="COM7", vid=0x2341, pid=0x1002),
        SimpleNamespace(device="COM1", vid=None, pid=None),
    ]
    import serial.tools.list_ports as lp

    monkeypatch.setattr(lp, "comports", lambda: ports)
    assert serial_link.find_port() == "COM7"
    assert serial_link.find_port("com3") == "COM3"
    assert serial_link.find_port("COM9") is None
    monkeypatch.setattr(lp, "comports", lambda: ports[2:])
    assert serial_link.find_port() is None


@pytest.mark.parametrize("pid", [0x0043, 0x0001])
def test_find_port_picks_uno_r3(monkeypatch, pid):
    """The team's UNO R3 (16U2 USB chip, 0x2341:0x0043 or :0x0001) beats clones and others."""
    ports = [
        SimpleNamespace(device="COM3", vid=0x1A86, pid=0x7523),  # CH340 clone
        SimpleNamespace(device="COM4", vid=0x046D, pid=0x0825),  # a webcam
        SimpleNamespace(device="COM8", vid=0x2341, pid=pid),
        SimpleNamespace(device="COM1", vid=None, pid=None),
    ]
    import serial.tools.list_ports as lp

    monkeypatch.setattr(lp, "comports", lambda: ports)
    assert rank_port(0x2341, pid) is not None
    assert serial_link.find_port() == "COM8"


def test_clock_map_uses_minimum_latency():
    cm = ClockMap(window_s=10)
    # board ms 1000 arrives at engine 5.020 (20 ms latency), later 2000 at 6.001
    assert cm.to_engine(1000, 5.020) == pytest.approx(5.020)
    assert cm.to_engine(2000, 6.001) == pytest.approx(6.001)
    # a late line (arrives 80 ms late) is mapped back onto the board's timeline
    assert cm.to_engine(3000, 7.081) == pytest.approx(7.001)
