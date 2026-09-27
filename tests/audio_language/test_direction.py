"""Side estimates use speech-time sensor changes, not fixed sensor offsets."""

import numpy as np
from attune.audio.direction import SpeechDirection
from attune.audio.service import AudioService


def _quiet(estimator: SpeechDirection, start: float = 0) -> float:
    for i in range(20):
        estimator.observe(start + i * 0.05, 220, 300)
        estimator.side(start + i * 0.05, False)
    return start + 1.0


def test_speech_side_uses_each_sensor_floor_and_vad():
    estimator = SpeechDirection(side_db=3)
    t = _quiet(estimator)
    assert estimator.side(t, False) == "none"
    for i in range(8):
        estimator.observe(t + i * 0.05, 460, 320)
        side = estimator.side(t + i * 0.05, True)
    assert side == "left"

    t += 0.5
    for i in range(8):
        estimator.observe(t + i * 0.05, 240, 530)
        side = estimator.side(t + i * 0.05, True)
    assert side == "right"


def test_weak_balanced_stale_and_motor_levels_are_inconclusive():
    estimator = SpeechDirection(side_db=3)
    t = _quiet(estimator)
    for i in range(8):
        estimator.observe(t + i * 0.05, 225, 315)
        side = estimator.side(t + i * 0.05, True)
    assert side == "none"
    t += 0.5
    for i in range(8):
        estimator.observe(t + i * 0.05, 470, 550)
        side = estimator.side(t + i * 0.05, True)
    assert side == "none"
    assert estimator.side(t + 1, True) == "none"
    estimator.observe(t + 1.05, 900, 0, motor_on=True)
    assert estimator.side(t + 1.05, True) == "none"


def test_no_direction_before_quiet_baseline_or_after_reset():
    estimator = SpeechDirection(side_db=3)
    for i in range(8):
        estimator.observe(i * 0.05, 450, 100)
        assert estimator.side(i * 0.05, True) == "none"
    estimator.reset()
    assert estimator.side(1, True) == "none"


def test_audio_service_publishes_direction_only_for_confirmed_speech(config, bus):
    service = AudioService(bus, config, vad=lambda _: 0.9)
    for i in range(20):
        service._handle(
            "sensors.levels",
            {"t": i * 0.05, "left": 220, "right": 300, "motor_on": False},
            0,
        )
    for i in range(8):
        t = 1 + i * 0.032
        service._handle(
            "sensors.levels",
            {"t": t, "left": 450, "right": 320, "motor_on": False},
            0,
        )
        service._frame(np.zeros(512, np.float32), t, 0)
    directions = [
        event["side"]
        for topic, event in bus.events
        if topic == "audio.speech_direction"
    ]
    assert directions[:7] == ["none"] * 7
    assert directions[-1] == "left"
