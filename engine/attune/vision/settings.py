"""Settings for Section 1, read from the [vision] and [fusion] tables of config/attune.toml.

Defaults match config/attune.example.toml and the build plan. Unknown keys are
an error, so a typo in the config can't silently fall back to a default.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass
class VisionSettings:
    camera_name: str = "Logitech"
    camera_fallback_any: bool = True  # use another camera if the named one is missing
    width: int = 1920
    height: int = 1080
    fps: int = 30
    det_size: int = 960
    det_score: float = 0.5
    det_low_score: float = 0.3  # second-stage matches for existing tracks (ByteTrack)
    nms: float = 0.4
    min_face_px: int = 36
    min_crop_px: int = 60
    max_yaw: float = 0.35  # nose offset / eye distance; about 35 degrees
    min_sharpness: float = 25.0  # Laplacian variance of the aligned crop
    min_brightness: float = 45.0
    match_threshold: float = 0.45
    match_margin: float = 0.08
    match_hits: int = 3  # within 1 s
    recheck_s: float = 2.0
    recheck_fails: int = 3
    max_rec_per_s: float = 10.0
    reid_threshold: float = 0.5
    track_survive_s: float = 1.0
    lost_list_s: float = 10.0
    edge_frac: float = 0.10
    enroll_s: float = 5.0
    enroll_crops: int = 8
    enroll_min_crops: int = 5
    lip_faces: int = 4
    lip_window_s: float = 1.0
    color_steady_s: float = 1.0
    det_model: str = "models/faces/buffalo_l/det_10g.onnx"
    rec_model: str = "models/faces/buffalo_l/w600k_r50.onnx"
    landmarker_model: str = "models/faces/mediapipe/face_landmarker.task"
    people_dir: str = "data/people"
    use_gpu: bool = True


@dataclass
class FusionSettings:
    lip_talking: float = 0.03
    lip_uncertain: float = 0.015
    hold_s: float = 0.5
    switch_ratio: float = 1.5
    voice_match: float = 0.5
    offscreen_after_s: float = 1.0
    offscreen_exit_memory_s: float = 30.0
    side_db: float = 3.0
    you_level_db: float | None = None  # set by calibration; None disables the "You" case
    you_balance_db: float = 3.0
    harvest_after_s: float = 1.5
    first_words_wait_ms: float = 300.0
    min_segment_s: float = 0.8  # shorter speaker pieces of a caption merge into a neighbour
    rate_hz: float = 15.0
    sync_min_corr: float = 0.3
    av_offset_s: float = 0.0  # audio lags video by this much; set by the clap test
    speech_hangover_s: float = 0.4


def _load(cls, table: dict[str, Any] | None):
    table = dict(table or {})
    known = {f.name for f in fields(cls)}
    unknown = set(table) - known
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**table)


def load_settings(config: dict[str, Any] | None) -> tuple[VisionSettings, FusionSettings]:
    """Build both settings objects from the whole config dict."""
    config = config or {}
    return _load(VisionSettings, config.get("vision")), _load(FusionSettings, config.get("fusion"))
