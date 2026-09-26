import importlib.util
from pathlib import Path

import numpy as np
import pytest
from attune.alerts.rhythm import RhythmDetector, RhythmEvidence
from attune.alerts.rules import AlertRules

spec = importlib.util.spec_from_file_location(
    "tones", Path(__file__).parents[2] / "scripts/make_test_tones.py"
)
tones = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tones)


@pytest.mark.parametrize("kind", ["T3", "T4"])
@pytest.mark.parametrize("frequency", [520, 3100])
def test_generated_rhythm(config, kind, frequency):
    detector = RhythmDetector(config["rhythm"])
    data = np.asarray(tones.samples(kind, frequency=frequency), np.float32)
    best = 0
    for start in range(0, len(data), 320):
        e = detector.feed(data[start : start + 320])
        best = max(best, e.t3_cycles if kind == "T3" else e.t4_cycles)
    assert best >= 2


def test_noise_and_wrong_frequency_do_not_confirm(config):
    for data in (
        np.random.default_rng(1).normal(0, 0.1, 320000),
        tones.samples("T3", frequency=1000),
    ):
        d = RhythmDetector(config["rhythm"])
        e = d.feed(np.asarray(data, np.float32))
        assert not e.t3_cycles and not e.t4_cycles


def test_smoke_needs_two_windows_and_beeps(config):
    r = AlertRules(config["alerts"], 3)
    assert not r.evaluate(1, {"Fire alarm": 0.8}, RhythmEvidence(beeps=2))
    events = r.evaluate(1.5, {"Fire alarm": 0.8}, RhythmEvidence(beeps=2), (10, 2))
    assert events[0][1]["kind"] == "smoke"
    assert events[1] == ("hw.pattern", {"name": "T3", "side": "L"})


def test_watch_is_only_status_and_motor_is_ignored(config):
    r = AlertRules(config["alerts"], 3)
    assert r.evaluate(0, {"Fire alarm": 0.4}, RhythmEvidence())[0][0] == "status.part"
    assert not r.evaluate(1, {"Fire alarm": 1}, RhythmEvidence(t3_cycles=2), motor_on=True)


def test_acknowledge_realert_and_clear(config):
    r = AlertRules(config["alerts"], 3)
    e = r.evaluate(0, {}, RhythmEvidence(t3_cycles=2))
    key = e[0][1]["alert_id"]
    assert r.acknowledge(key, 1)[0][1]["state"] == "acknowledged"
    for t in range(2, 31):
        assert not r.evaluate(t, {}, RhythmEvidence(t3_cycles=2))
    assert r.evaluate(31, {}, RhythmEvidence(t3_cycles=2))[0][1]["state"] == "start"
    assert r.tick(46)[0][1]["state"] == "clear"


def test_doorbell_speech_music_and_rest(config):
    r = AlertRules(config["alerts"], 3)
    for blocked in ("Speech", "Music"):
        assert not r.evaluate(0, {"Doorbell": 0.8, blocked: 0.5}, RhythmEvidence())
    e = r.evaluate(0, {"Ding-dong": 0.8}, RhythmEvidence(), (1, 10))
    assert e[0][1]["side"] == "right"
    assert not r.evaluate(1, {"Ding-dong": 0.8}, RhythmEvidence(), (1, 10))


def test_alert_service_replayed_pcm_and_motor_suppression(config, bus):
    from attune.alerts.service import AlertService

    class Model:
        def score(self, pcm):
            return {"Fire alarm": 0.9}

    service = AlertService(bus, config, model=Model())
    service.clock = lambda: 0.0
    audio = np.asarray(tones.samples("T3", cycles=3), np.float32)
    for offset in range(0, len(audio), 1600):
        t = offset / 32000
        service._handle("sensors.levels", {"t": t, "left": 100, "right": 10, "motor_on": False}, 0)
        service._handle(
            "audio.block",
            {"t": t, "sample_rate": 32000, "samples": audio[offset : offset + 1600]},
            0,
        )
    alerts = [e for topic, e in bus.events if topic == "alert" and e["state"] == "start"]
    assert alerts and alerts[0]["kind"] == "smoke"
    service._handle("session.forget", {}, 0)
    bus.events.clear()
    for offset in range(0, len(audio), 1600):
        t = offset / 32000
        service._handle("sensors.levels", {"t": t, "left": 100, "right": 10, "motor_on": True}, 0)
        service._handle(
            "audio.block",
            {"t": t, "sample_rate": 32000, "samples": audio[offset : offset + 1600]},
            0,
        )
    assert not [e for topic, e in bus.events if topic == "alert"]
