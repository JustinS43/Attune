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
