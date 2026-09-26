"""Validated, atomic venue profiles containing measurements only."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

ALLOWED = {
    "level_confirmed",
    "mic_peak_dbfs",
    "you_dbfs",
    "other_dbfs",
    "you_threshold_dbfs",
    "left_gain",
    "right_gain",
    "av_offset_s",
    "noise_rms",
    "face_scores",
    "complete_steps",
}

STEPS = ("level", "mic", "you", "other", "balance", "claps", "noise", "faces")


def validate(values: dict) -> None:
    """Accept only finite, correctly typed venue measurements, never identities."""
    if not isinstance(values, dict) or not set(values) <= ALLOWED:
        raise ValueError("invalid venue profile")
    for key, value in values.items():
        if key == "level_confirmed":
            valid = type(value) is bool
        elif key == "complete_steps":
            valid = (
                isinstance(value, list)
                and all(isinstance(step, str) and step in STEPS for step in value)
                and len(value) == len(set(value))
            )
        elif key == "face_scores":
            valid = isinstance(value, list) and all(
                type(score) in (float, int) and math.isfinite(score) and 0 <= score <= 1
                for score in value
            )
        else:
            valid = type(value) in (float, int) and math.isfinite(value)
            if valid and key in {"left_gain", "right_gain"}:
                valid = value > 0
            if valid and key == "noise_rms":
                valid = value >= 0
        if not valid:
            raise ValueError(f"invalid venue measurement: {key}")


def save(root: Path, name: str, values: dict) -> Path:
    """Persist a profile atomically; reject path traversal and nonfinite measurements."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ValueError("invalid venue profile")
    validate(values)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.json"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("profile escapes storage directory")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, allow_nan=False, indent=2))
    temporary.replace(path)
    return path


def load(root: Path, name: str) -> dict:
    """Load a profile without reading other runtime data."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ValueError("invalid venue name")
    path = root / f"{name}.json"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("profile escapes storage directory")
    if not path.exists():
        return {}
    result = json.loads(path.read_text())
    validate(result)
    return result
