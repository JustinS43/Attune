"""Settings for the enrollment station, read from the [enroll] table of config/attune.toml.

Section 1 - Vision. TODO: V-23, A-21. Defaults match config/attune.example.toml. Unknown
keys are an error, so a typo can't silently fall back to a default.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

SOURCES = ("station", "glasses")

DEFAULT_SENTENCE = (
    "Every morning I walk my dog through the park, buy fresh bread and orange juice, "
    "and enjoy watching children play by the quiet river."
)


@dataclass
class EnrollSettings:
    # "station": people are saved at the laptop's own camera and mic; "glasses": the glasses
    # camera and mic save them (the original P-29 flow).
    source: str = "station"
    camera_name: str = "OV02E10"  # laptop camera, matched against the device name
    mic_name: str = "Microphone Array"  # laptop mic (WASAPI), matched against the device name
    camera_width: int = 1280
    camera_height: int = 720
    camera_fps: int = 30
    open_timeout_s: float = 6.0  # no frames / no audio this long after opening: busy or missing
    # Test and demo stand-ins: a video file for the camera, a WAV file for the mic.
    camera_source: str = ""
    mic_source: str = ""
    # Live preview for the phone that started the save (memory only, never stored).
    preview_width: int = 360  # a 3:4 portrait crop from the middle of the frame
    preview_fps: float = 12.0
    preview_quality: int = 70
    # Face step. The capture window (like [vision] enroll_s) starts at the first good crop.
    face_s: float = 5.0
    face_timeout_s: float = 30.0  # no usable face this long: the face step fails, with why
    face_rate_hz: float = 8.0  # face prints per second at most
    center_tol: float = 0.2  # face centre further than this (share of the preview) off-centre
    min_face_share: float = 0.22  # face narrower than this share of the preview: come closer
    max_face_share: float = 0.8  # wider than this: move back a little
    dominant_ratio: float = 1.3  # the person must be this much wider than any other face
    same_person: float = 0.4  # every print must be this close to the session's first prints
    # Identity check: the station face against the glasses track the save started from.
    identity_match: float = 0.3
    decision_timeout_s: float = 60.0  # "That isn't the person you were looking at" waits this long
    # Voice step: read the sentence into the laptop mic; [voice] enroll_s of voiced speech.
    sentence: str = DEFAULT_SENTENCE
    voice_timeout_s: float = 40.0
    vad_start: float = 0.5
    vad_end: float = 0.35
    quiet_db: float = -42.0  # voiced speech quieter than this (dBFS RMS): speak up
    loud_db: float = -8.0  # louder than this, or clipping: a bit softer
    clip_level: float = 0.98  # a sample at or above this counts as clipped
    min_snr_db: float = 10.0  # speech this close to the room's noise floor: too noisy
    pause_hint_s: float = 1.5  # no speech this long once started: keep talking
    level_hz: float = 15.0  # level meter messages per second


def load_enroll_settings(config: dict[str, Any] | None) -> EnrollSettings:
    """The [enroll] table as settings; raises on unknown keys or an unknown source."""
    table = dict((config or {}).get("enroll") or {})
    known = {f.name for f in fields(EnrollSettings)}
    unknown = set(table) - known
    if unknown:
        raise ValueError(f"Unknown [enroll] keys: {sorted(unknown)}")
    s = EnrollSettings(**table)
    if s.source not in SOURCES:
        raise ValueError(f"[enroll] source must be one of {SOURCES}, not {s.source!r}")
    return s
