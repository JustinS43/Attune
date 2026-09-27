"""Event and message types used by Section 1 (Vision and Fusion).

These mirror docs/contracts.md field for field. When Section 4 lands
`attune.core.contracts` (TODO P-01), import from there instead and delete
the duplicates here.

Events from other sections may arrive as dataclasses or plain dicts, so
read them with `get()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

# ---- Topics (docs/contracts.md, section 2) ----
VISION_FRAME = "vision.frame"
VISION_TRACKS = "vision.tracks"
VISION_TRACK_LOST = "vision.track_lost"
VISION_APPEARANCE = "vision.appearance"
VISION_DESCRIPTION = "vision.description"
AUDIO_VAD = "audio.vad"
AUDIO_LEVEL = "audio.level"
AUDIO_BLOCK = "audio.block"
AUDIO_TRANSCRIPT = "audio.transcript"
AUDIO_VOICE_MATCH = "audio.voice_match"
VOICE_HARVEST = "voice.harvest"
CAPTION = "caption"
CAPTION_RETRACT = "caption.retract"
SCENE = "scene"
NAME_PROPOSAL = "name.proposal"
NAME_EVIDENCE = "name.evidence"
SENSORS_LEVELS = "sensors.levels"
ENROLL_RESULT = "enroll.result"
ENROLL_PROGRESS = "enroll.progress"
PERSON_CHANGED = "person.changed"
SAVE_REQUEST = "save.request"
SESSION_FORGET = "session.forget"
PAUSED = "paused"
COMMAND = "command"
STATUS_PART = "status.part"
SPEAKER_CLOUD = "speaker.cloud"  # V-32: cloud captions' per-word speaker tags
CLOUD_STATE = "cloud.state"  # V-32: whether cloud captions are on


def get(event: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dataclass-like object or a dict."""
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


# ---- Published by Vision ----
@dataclass
class Frame:
    frame_no: int
    t: float
    image: np.ndarray  # BGR, full camera resolution


@dataclass
class Track:
    track_id: int
    box: list[float]  # [x, y, w, h] in camera pixels
    face_px: int
    lip_score: float
    person_id: str | None
    name: str | None
    match_score: float
    status: str  # unknown, proposed, named, enrolled
    mouth_open: float | None = None  # latest mouth-open ratio, for the in-time check
    # Light-ASD speaking logit (V-22): > 0 means talking in time with the sound. None when
    # the face isn't scored (model off, face too small, too little history) or it's stale.
    asd_score: float | None = None


@dataclass
class Tracks:
    frame_no: int
    t: float
    tracks: list[Track]


@dataclass
class TrackLost:
    track_id: int
    t: float
    side: str  # left, right, none


@dataclass
class Appearance:
    track_id: int
    color: str
    crop: np.ndarray


@dataclass
class EnrollResult:
    person_id: str | None
    part: str  # face, voice
    ok: bool
    reason: str = ""
    track_id: int | None = None  # the requested track, so Section 2 can pair voice consent
    source: str = "glasses"  # "station": saved at the laptop (V-23 / A-21)
    session_id: str | None = None  # the station save it belongs to


@dataclass
class PersonChanged:
    person_id: str
    name: str
    action: str  # enrolled, renamed, deleted


@dataclass
class StatusPart:
    part: str
    ok: bool
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


# ---- Published by Fusion ----
@dataclass
class Speaker:
    kind: str  # you, you_typed, face, probable_face, offscreen, someone
    track_id: int | None = None
    person_id: str | None = None
    label: str = ""
    side: str = "none"


@dataclass
class Caption:
    utt_id: str
    speaker: Speaker
    text: str
    final: bool
    lang: str | None
    words: list[tuple[str, float, float]]


@dataclass
class CaptionRetract:
    """A caption segment id sent earlier is no longer part of its utterance."""

    utt_id: str


@dataclass
class FaceState:
    track_id: int
    box: list[float]
    label: str
    status: str
    lip_score: float
    is_speaker: bool
    dashed: bool


@dataclass
class Offscreen:
    person_id: str | None
    label: str
    side: str


@dataclass
class Scene:
    frame_no: int
    t: float
    faces: list[FaceState]
    offscreen: list[Offscreen]
    you_speaking: bool


@dataclass
class VoiceHarvest:
    person_id: str
    t0: float
    t1: float
    talkers: int = 1  # most faces talking at once over the span (A-21 adapts only on 1)
