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


def save(root: Path, name: str, values: dict) -> Path:
    """Persist a profile atomically; reject path traversal and nonfinite measurements."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not set(values) <= ALLOWED:
        raise ValueError("invalid venue profile")

    def validate(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite measurement")
        if isinstance(value, dict):
            for v in value.values():
                validate(v)
        elif isinstance(value, list):
            for v in value:
                validate(v)

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
    if not isinstance(result, dict) or not set(result) <= ALLOWED:
        raise ValueError("invalid venue profile")
    return result
