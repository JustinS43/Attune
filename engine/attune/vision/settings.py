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
    # Lip score lines (the band-passed score of vision/mouth.py, V-19). A face is moving
    # like a talker at >= lip_talking; lip_uncertain-lip_talking is the "probable" band.
    lip_talking: float = 0.012
    lip_uncertain: float = 0.008
    # Per-face noise floor: the lip_floor_pct percentile of this face's own lip score
    # over the last lip_floor_s (once it has lip_floor_min_s of it). A face must also
    # reach lip_floor_ratio x its floor, so a face that always jitters needs more.
    lip_floor_s: float = 15.0
    lip_floor_min_s: float = 3.0
    lip_floor_pct: float = 10.0
    lip_floor_ratio: float = 1.5
    # Talking, not a one-off mouth movement: while speech is heard the face must move in
    # time with it (at least talk_cover_share of the ticks since it started, and now) for
    # talk_confirm_s if it started within talk_onset_s of the speech starting, otherwise
    # (it started in the middle of someone's speech) for talk_sustain_s.
    talk_cover_share: float = 0.7
    talk_confirm_s: float = 0.25
    talk_onset_s: float = 0.4
    talk_sustain_s: float = 1.5
    # A mouth open wider than this (open ratio) in the last second is a yawn or a
    # laugh, not speech.
    lip_open_max: float = 0.6
    # A head moving faster than this (face widths per second) blurs the lips and their
    # landmarks jump, so its lips count as not measured.
    head_motion_max: float = 0.8
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
    # In time: lips and loudness (both band-passed to syllable rates) correlate at least
    # this much over the last av_window_s. With sound available this is required, not
    # just "not contradicted", before speech goes to a face.
    sync_min_corr: float = 0.3
    av_window_s: float = 1.5
    # Off only for a film reel played over unrelated audio (the demo reels with talk.wav):
    # there no face can be in time with the sound, so lips are judged by movement alone.
    require_sync: bool = True
    av_offset_s: float = 0.0  # audio lags video by this much; set by the clap test
    speech_hangover_s: float = 0.4
    # Voice evidence (this utterance's audio.voice_match against the face's print):
    # a best match to another voice, or a best score below voice_reject when the face
    # has a session print, vetoes the face; a match to the face's own print lets it
    # speak with the probable-band mouth bar.
    voice_reject: float = 0.25
    # Session voice prints are only harvested from a face this far in time (and passing
    # every talking check), so a silent face never learns a background voice.
    harvest_min_corr: float = 0.4


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
