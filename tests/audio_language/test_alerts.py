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


def test_short_completed_gaps_cannot_count_as_spaced_beeps(config):
    detector = RhythmDetector(config["rhythm"])
    tone = np.sin(2 * np.pi * 3100 * np.arange(16000) / 32000).astype(np.float32) * 0.1
    data = np.tile(np.concatenate((tone, np.zeros(3200, np.float32))), 6)
    best = 0
    for offset in range(0, len(data), 320):
        evidence = detector.feed(data[offset : offset + 320])
        best = max(best, evidence.beeps)
        assert evidence.t3_cycles < 2
    assert best == 1


@pytest.mark.parametrize("kind", ["T3", "T4"])
def test_second_group_confirms_without_waiting_for_final_rest(config, kind):
    detector = RhythmDetector(config["rhythm"])
    data = np.asarray(tones.samples(kind, cycles=2), np.float32)
    confirmed = None
    for offset in range(0, len(data), 320):
        evidence = detector.feed(data[offset : offset + 320])
        cycles = evidence.t3_cycles if kind == "T3" else evidence.t4_cycles
        if cycles >= 2:
            confirmed = (offset + 320) / 32000
            break
    assert confirmed is not None and confirmed <= 7.0


def test_short_intercycle_rest_does_not_confirm_two_groups(config):
    detector = RhythmDetector(config["rhythm"])
    cycle = np.asarray(tones.samples("T4", cycles=1), np.float32)
    data = np.tile(cycle[: round(0.8 * 32000)], 4)
    for offset in range(0, len(data), 320):
        assert detector.feed(data[offset : offset + 320]).t4_cycles < 2


def test_rhythm_silence_does_not_delay_clear_or_realert(config):
    rules = AlertRules(config["alerts"], 3)
    events = rules.evaluate(1, {}, RhythmEvidence(t4_cycles=2), (10, 1))
    key = events[0][1]["alert_id"]
    rules.acknowledge(key, 1)
    # The sound continues to second 28, then only its normal pause remains.
    assert not rules.evaluate(28, {}, RhythmEvidence(t4_cycles=2))
    assert not rules.evaluate(31, {}, RhythmEvidence(t4_cycles=2, quiet_s=3))
    assert rules.tick(43)[0][1]["state"] == "clear"


def test_missing_direction_in_quiet_hop_preserves_alarm_side(config):
    rules = AlertRules(config["alerts"], 3)
    rules.evaluate(1, {}, RhythmEvidence(t3_cycles=2), (10, 1))
    assert not rules.evaluate(1.5, {}, RhythmEvidence(t3_cycles=2, quiet_s=0.5))
    assert rules.active["smoke"]["side"] == "left"


@pytest.mark.parametrize("kind", ["T3", "T4"])
def test_rhythm_only_service_confirms_by_seven_seconds(config, bus, kind):
    from attune.alerts.service import AlertService

    class Model:
        def score(self, pcm):
            return {}

    service = AlertService(bus, config, model=Model())
    service.clock = lambda: 0.0
    data = np.asarray(tones.samples(kind), np.float32)
    for offset in range(0, 7 * 32000, 1600):
        t = offset / 32000
        service._handle("sensors.levels", {"t": t, "left": 100, "right": 10, "motor_on": False}, 0)
        service._handle(
            "audio.block",
            {"t": t, "sample_rate": 32000, "samples": data[offset : offset + 1600]},
            0,
        )
    starts = [e for topic, e in bus.events if topic == "alert" and e["state"] == "start"]
    assert len(starts) == 1
    assert starts[0]["kind"] == ("smoke" if kind == "T3" else "co")
    assert starts[0]["side"] == "left"


def test_classifier_failure_consumes_window_and_recovers(config, bus):
    from attune.alerts.service import AlertService

    class Model:
        calls = 0

        def score(self, pcm):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("classifier temporarily unavailable")
            return {"Doorbell": 0.8}

    model = Model()
    service = AlertService(bus, config, model=model)
    service.clock = lambda: 0.0
    service._handle(
        "audio.block", {"t": 0, "sample_rate": 32000, "samples": np.zeros(32000, np.float32)}, 0
    )
    assert len(service.audio) == 16000
    assert "rhythm-only" in service.worker.error
    service._handle(
        "audio.block", {"t": 1, "sample_rate": 32000, "samples": np.zeros(16000, np.float32)}, 0
    )
    assert model.calls == 2
    assert service.worker.error == ""
    assert any(topic == "alert" and e["kind"] == "doorbell" for topic, e in bus.events)


def test_missing_classifier_still_starts_rhythm_worker(config, bus, monkeypatch):
    import attune.alerts.service as alert_service

    def missing(_):
        raise FileNotFoundError("local classifier weights missing")

    config["sound_model"] = {}
    monkeypatch.setattr(alert_service, "SoundModel", missing)
    service = alert_service.AlertService(bus, config)
    try:
        service.start()
        assert service.worker.thread.is_alive()
        assert "rhythm-only" in service.worker.error
    finally:
        service.stop()


@pytest.mark.parametrize("hop", [0, -0.5, 1.5, 0.015])
def test_alert_hop_must_cover_whole_frames(config, bus, hop):
    from attune.alerts.service import AlertService

    config["alerts"]["hop_s"] = hop
    with pytest.raises(ValueError, match="whole rhythm frames"):
        AlertService(bus, config)


def test_sound_model_gets_up_to_ten_seconds_of_context(config, bus):
    """EfficientAT needs clip-length input; the service passes the latest 1-10 s."""
    from attune.alerts.service import AlertService

    class Model:
        def __init__(self):
            self.lengths = []

        def score(self, pcm):
            self.lengths.append(len(pcm))
            return {}

    model = Model()
    service = AlertService(bus, config, model=model)
    service.clock = lambda: 0.0
    audio = np.zeros(12 * 32000, np.float32)
    for offset in range(0, len(audio), 1600):
        service._handle(
            "audio.block",
            {"t": offset / 32000, "sample_rate": 32000, "samples": audio[offset : offset + 1600]},
            0,
        )
    assert model.lengths[0] == 32000
    assert model.lengths == sorted(model.lengths)
    assert model.lengths[-1] == 10 * 32000


def test_taps_cannot_clear_a_sounding_alarm(config):
    """A-28: the rig's own taps hide the room, so tapped windows can't show that an alarm
    stopped. The alert holds through them and clears only after heard quiet."""
    r = AlertRules(config["alerts"], 3)
    assert r.evaluate(0, {}, RhythmEvidence(t3_cycles=2), (10, 1))[0][1]["state"] == "start"
    for i in range(1, 121):  # a minute of tapping, a window every 0.5 s
        assert not r.evaluate(i / 2, {}, RhythmEvidence(), motor_on=True)
    assert r.active["smoke"]["side"] == "left"
    quiet = config["alerts"]["clear_quiet_s"]
    assert not r.tick(60 + quiet - 0.5)
    out = r.tick(60 + quiet)
    assert out[0][1]["state"] == "clear"
    assert out[-1] == ("hw.stop", {})


def test_alarm_holds_while_the_rig_taps_until_got_it(config, bus):
    """A-28 on the service: a minute of T3 while the rig taps T3 back (every window is
    motor-flagged) is one alert, not a 15 s on / 10 s off cycle. It stays on after the alarm
    stops until "Got it"; then the room is heard quiet and it clears."""
    from attune.alerts.service import AlertService

    class Model:
        def score(self, pcm):
            return {}

    now = [0.0]
    service = AlertService(bus, config, model=Model())
    service.clock = lambda: now[0]
    audio = np.concatenate(
        (np.asarray(tones.samples("T3", cycles=15), np.float32), np.zeros(40 * 32000, np.float32))
    )
    seen: list[tuple[float, str, dict]] = []
    tapping = acked = False
    for offset in range(0, len(audio), 1600):
        t = now[0] = offset / 32000
        if not acked and t >= 70:  # the wearer taps "Got it" 10 s after the alarm stopped
            alert_id = next(e["alert_id"] for topic, e in bus.events if topic == "alert")
            service._handle("command", {"name": "alert.ack", "args": {"alert_id": alert_id}}, 0)
            acked = True
        service._handle(
            "sensors.levels", {"t": t, "left": 100, "right": 10, "motor_on": tapping}, 0
        )
        service._handle(
            "audio.block",
            {"t": t, "sample_rate": 32000, "samples": audio[offset : offset + 1600]},
            0,
        )
        for topic, e in bus.events[len(seen) :]:
            seen.append((t, topic, e))
            if topic == "hw.pattern":
                tapping = True  # the rig plays T3 until STOP
            elif topic == "hw.stop":
                tapping = False
    alerts = [(t, e["state"]) for t, topic, e in seen if topic == "alert"]
    assert [s for _, s in alerts] == ["start", "acknowledged", "clear"]
    assert alerts[0][0] < 10
    assert 85 <= alerts[2][0] <= 88  # 15 s of heard quiet after the last tapped window


def test_knock_fires_through_speech_with_the_bell_pattern(config):
    """A-30: people call out while they knock, so speech must not block a knock."""
    r = AlertRules(config["alerts"], 3)
    assert not r.evaluate(0, {"Knock": 0.09}, RhythmEvidence(), (10, 1))
    e = r.evaluate(0.5, {"Knock": 0.3, "Speech": 0.8}, RhythmEvidence(), (10, 1))
    assert e[0] == ("alert", {**e[0][1], "kind": "knock", "side": "left", "state": "start"})
    assert e[0][1]["confidence"] == 0.3
    assert e[1] == ("hw.pattern", {"name": "BELL", "side": "L"})


def test_music_blocks_a_knock(config):
    r = AlertRules(config["alerts"], 3)
    assert not r.evaluate(0, {"Knock": 0.9, "Music": 0.5}, RhythmEvidence(), (10, 1))
    assert r.evaluate(0.5, {"Knock": 0.9, "Music": 0.49}, RhythmEvidence(), (10, 1))


def test_knock_rest_period_and_quiet_clear(config):
    config["alerts"]["clear_quiet_s"] = 2.0  # shorter than the rest, so the rest is what holds
    r = AlertRules(config["alerts"], 3)
    assert r.evaluate(0, {"Knock": 0.3}, RhythmEvidence())[0][1]["state"] == "start"
    assert not r.tick(1.9)
    cleared = r.tick(2)
    assert [e["state"] for topic, e in cleared if topic == "alert"] == ["clear"]
    assert cleared[-1] == ("hw.stop", {})
    # Still within knock_rest_s (10 s) of the last knock alert: no new alert...
    assert not r.evaluate(5, {"Knock": 0.3}, RhythmEvidence())
    assert "knock" not in r.active
    # ...while a doorbell has its own rest and still fires.
    assert r.evaluate(5, {"Doorbell": 0.8}, RhythmEvidence())[0][1]["kind"] == "doorbell"
    assert r.evaluate(10, {"Knock": 0.3}, RhythmEvidence())[0][1]["kind"] == "knock"


def test_knock_stays_up_while_heard_and_clears_after_quiet(config):
    r = AlertRules(config["alerts"], 3)
    r.evaluate(0, {"Knock": 0.3}, RhythmEvidence())
    assert not r.evaluate(4, {"Knock": 0.2}, RhythmEvidence())  # still knocking: no repeat
    assert not r.tick(18.9)
    assert [e["state"] for topic, e in r.tick(19) if topic == "alert"] == ["clear"]


def test_knock_and_doorbell_fire_side_by_side(config):
    r = AlertRules(config["alerts"], 3)
    e = r.evaluate(0, {"Doorbell": 0.8, "Knock": 0.3}, RhythmEvidence(), (1, 10))
    starts = [ev["kind"] for topic, ev in e if topic == "alert"]
    assert starts == ["doorbell", "knock"]
    assert [ev for topic, ev in e if topic == "hw.pattern"] == [{"name": "BELL", "side": "R"}] * 2


def test_knock_side_comes_from_the_event_window_levels(config, bus):
    """A knock has no tone for the rhythm frames, so its side uses the sensor balance."""
    from attune.alerts.service import AlertService

    class Model:
        def score(self, pcm):
            return {"Knock": 0.3}

    service = AlertService(bus, config, model=Model())
    service.clock = lambda: 0.0
    for offset in range(0, 32000, 1600):
        t = offset / 32000
        service._handle("sensors.levels", {"t": t, "left": 10, "right": 100, "motor_on": False}, 0)
        service._handle(
            "audio.block",
            {"t": t, "sample_rate": 32000, "samples": np.zeros(1600, np.float32)},
            0,
        )
    alerts = [e for topic, e in bus.events if topic == "alert"]
    assert [(e["kind"], e["side"], e["state"]) for e in alerts] == [("knock", "right", "start")]
    assert ("hw.pattern", {"name": "BELL", "side": "R"}) in [
        (topic, e) for topic, e in bus.events if topic == "hw.pattern"
    ]
