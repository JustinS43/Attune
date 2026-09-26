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
    # Light-ASD (V-22): who is talking from lips and sound together (vision/asd.py). With
    # it off, or its weights missing, tracks carry no asd_score and fusion uses the lip score.
    asd_enabled: bool = True
    asd_model: str = "models/light_asd/finetuning_TalkSet.model"
    asd_device: str = "cuda"  # or "cpu": about 75-100 ms per face per round
    asd_rate_hz: float = 5.0  # scoring rounds per second (all faces in one batch)
    asd_window_s: float = 1.5  # history each round looks at
    asd_score_s: float = 0.4  # the score is the mean over the newest part of the window
    asd_faces: int = 4  # largest faces scored
    asd_min_face_px: int = 40
    asd_max_gap_s: float = 0.2  # longest hole in a face's frames a window may have
    asd_min_fps: float = 12.0  # fewer frames a second than this: no score (vision too slow)
    asd_av_offset_s: float = 0.0  # audio lags video by this much
    asd_max_age_s: float = 0.6  # a score older than this isn't published


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
    # Light-ASD (V-22, fusion/asd_gate.py): a face with a fresh asd_score (a logit; > 0 is
    # talking) may be the speaker only from asd_on until it drops below asd_off. Faces
    # without one use the lip-score rules; asd_use = false ignores the scores entirely.
    asd_use: bool = True
    asd_on: float = 0.5
    asd_off: float = -0.5
    asd_fresh_s: float = 0.6  # by frame time
    asd_switch_margin: float = 1.0  # another face must score this much higher to take over
    asd_harvest: float = 1.5  # sure enough to harvest a voice print from
    you_level_db: float | None = None  # set by calibration; None disables the "You" case
    you_balance_db: float = 3.0
    harvest_after_s: float = 1.5
    first_words_wait_ms: float = 300.0
    min_segment_s: float = 0.8  # shorter speaker pieces of a caption merge into a neighbour
    # A caption segment already shown keeps its speaker across drafts. It only changes when
    # another known speaker covers this share of the segment's speech (by word time)...
    relabel_share: float = 0.7
    # ...or, for a segment shown as "Someone", when a known speaker covers this share.
    claim_share: float = 0.5
    # A word counts at most this long when measuring a caption piece: the streaming
    # recogniser stretches a draft's last word to the end of its audio chunk.
    max_word_s: float = 0.6
    # Words already shown move to a new segment (a turn change found late) only as part
    # of a run of another speaker at least this long.
    move_segment_s: float = 1.2
    utterance_memory_s: float = 60.0  # forget an unfinished utterance's segments after this
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
