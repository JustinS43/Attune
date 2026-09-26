import numpy as np
import pytest
from attune.calibration.profile import load, save
from attune.calibration.wizard import CalibrationService


def test_profile_atomic_roundtrip_and_validation(tmp_path):
    save(tmp_path, "venue", {"noise_rms": 0.02})
    assert load(tmp_path, "venue") == {"noise_rms": 0.02}
    for name in ("../bad", "/absolute"):
        with pytest.raises(ValueError):
            save(tmp_path, name, {})
    with pytest.raises(ValueError):
        save(tmp_path, "venue", {"noise_rms": float("nan")})
    assert load(tmp_path, "venue") == {"noise_rms": 0.02}


def test_noise_requires_full_duration(config, bus):
    s = CalibrationService(bus, config)
    s._handle("command", {"name": "calibrate.step", "args": {"step": "noise"}}, 0)
    s._handle("audio.block", {"sample_rate": 16000, "samples": np.ones(16000) * 0.02}, 0)
    with pytest.raises(ValueError):
        s.finish({})
    for _ in range(29):
        s._handle("audio.block", {"sample_rate": 16000, "samples": np.ones(16000) * 0.02}, 0)
    s.finish({})
    assert s.values["noise_rms"] == pytest.approx(0.02)
    assert "noise" in load(s.root, s.name)["complete_steps"]


def test_clap_offsets_and_level_need_manual_evidence(config, bus):
    s = CalibrationService(bus, config)
    s.step = "level"
    with pytest.raises(ValueError):
        s.finish({})
    s.finish({"confirmed": True})
    s.step = "claps"
    s.finish({"audio_times": [1.1, 2.1, 3.1], "video_times": [1, 2, 3]})
    assert s.values["av_offset_s"] == pytest.approx(0.1)


@pytest.mark.parametrize(
    "values",
    [
        {"noise_rms": float("nan")},
        {"noise_rms": -1},
        {"left_gain": 0},
        {"face_scores": {"person_1": 0.9}},
        {"face_scores": [float("inf")]},
        {"complete_steps": ["unknown"]},
        {"level_confirmed": "yes"},
    ],
)
def test_loaded_profiles_use_same_measurement_validation(tmp_path, values):
    import json

    (tmp_path / "venue.json").write_text(json.dumps(values))
    with pytest.raises(ValueError):
        load(tmp_path, "venue")
    with pytest.raises(ValueError):
        save(tmp_path, "venue", values)


def test_calibration_rms_weights_samples_in_variable_blocks(config, bus):
    service = CalibrationService(bus, config)
    service._handle("command", {"name": "calibrate.step", "args": {"step": "you"}}, 0)
    service._handle(
        "audio.block", {"sample_rate": 16000, "samples": np.ones(16000) * 0.1}, 0
    )
    service._handle(
        "audio.block", {"sample_rate": 16000, "samples": np.ones(48000) * 0.3}, 0
    )
    service.finish({})
    assert service.values["you_dbfs"] == pytest.approx(10 * np.log10(0.07))


def test_paused_calibration_cannot_restart_capture(config, bus):
    service = CalibrationService(bus, config)
    service._handle("paused", {"paused": True}, 0)
    service._handle("command", {"name": "calibrate.step", "args": {"step": "noise"}}, 0)
    assert service.step is None
    assert service.worker.error
    service._handle("paused", {"paused": False}, 0)
    service._handle("command", {"name": "calibrate.step", "args": {"step": "noise"}}, 0)
    assert service.step == "noise"
    assert not service.worker.error


def test_finish_queued_before_forget_cannot_save_profile(config, bus):
    service = CalibrationService(bus, config)
    service.step = "level"
    service.worker.generation = 1
    service._handle(
        "command",
        {"name": "calibrate.step", "args": {"step": "finish:level", "measurements": {"confirmed": True}}},
        0,
    )
    assert not (service.root / f"{service.name}.json").exists()
    service.finish({"confirmed": True}, generation=0)
    assert not (service.root / f"{service.name}.json").exists()


def test_failed_finish_reports_actionable_reason_and_can_retry(config, bus):
    service = CalibrationService(bus, config)
    service.step = "level"
    service._handle(
        "command", {"name": "calibrate.step", "args": {"step": "finish:level"}}, 0
    )
    assert "confirm the camera" in service.worker.error
    service._handle(
        "command",
        {"name": "calibrate.step", "args": {"step": "finish:level", "measurements": {"confirmed": True}}},
        0,
    )
    assert not service.worker.error
    assert service.step is None
    assert service._health()["detail"] == "Saved level"
