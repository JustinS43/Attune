import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "test_tone_generator", Path(__file__).resolve().parents[2] / "scripts" / "make_test_tones.py"
)
tones = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tones)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"frequency": 0},
        {"frequency": -520},
        {"frequency": float("nan")},
        {"frequency": float("inf")},
        {"frequency": 16000},
        {"rate": 0},
        {"rate": -1},
        {"rate": 32000.5},
        {"cycles": 0},
        {"cycles": 1.5},
        {"cycles": True},
    ],
)
def test_invalid_tone_parameters_do_not_write_file(tmp_path, kwargs):
    path = tmp_path / "tone.wav"
    with pytest.raises(ValueError):
        tones.write_tone(path, "T3", **kwargs)
    assert not path.exists()
