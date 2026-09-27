"""Event and message types: the code copy of docs/contracts.md.

Section 4 - Pages, Engine & Demo. TODO: P-01.

One dataclass per bus event and shared type, with the same names and fields as
docs/contracts.md, plus the topic, WebSocket message and command names. Field names
match Section 1's `attune.vision.types` exactly, so events built from either module
are interchangeable. Producers may also publish plain dicts; read events with `get()`.

This file and docs/contracts.md change together, in a [shared] PR, and only by
adding fields (with defaults) or events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Bus topics (contracts section 2)
# ---------------------------------------------------------------------------
VISION_FRAME = "vision.frame"
VISION_TRACKS = "vision.tracks"
VISION_TRACK_LOST = "vision.track_lost"
VISION_APPEARANCE = "vision.appearance"
VISION_DESCRIPTION = "vision.description"
AUDIO_BLOCK = "audio.block"
AUDIO_VAD = "audio.vad"
AUDIO_LEVEL = "audio.level"
AUDIO_TRANSCRIPT = "audio.transcript"
AUDIO_VOICE_MATCH = "audio.voice_match"
VOICE_HARVEST = "voice.harvest"
CAPTION = "caption"
# A caption segment id sent earlier is no longer part of its utterance: {utt_id}
CAPTION_RETRACT = "caption.retract"
CAPTION_TRANSLATION = "caption.translation"
SCENE = "scene"
NAME_PROPOSAL = "name.proposal"
NAME_EVIDENCE = "name.evidence"
ALERT = "alert"
REPLY_SUGGESTIONS = "reply.suggestions"
SENSORS_LEVELS = "sensors.levels"
SENSORS_TOUCH = "sensors.touch"
TOUCH_ACTION = "touch.action"
HW_PATTERN = "hw.pattern"
HW_STOP = "hw.stop"
HW_LINK = "hw.link"
SPEECH_OUT_PLAYING = "speech_out.playing"
REPLY_SPOKEN = "reply.spoken"
ENROLL_RESULT = "enroll.result"
# Save a person (double tap, P-29): progress of a running enrollment, and the consent request
# the engine sends to the phone and console after a double tap (and its cancellation).
ENROLL_PROGRESS = "enroll.progress"
SAVE_REQUEST = "save.request"
SAVE_CANCEL = "save.cancel"
PERSON_CHANGED = "person.changed"
# Enrollment station (V-23 / A-21 / P-35): saving a person at the laptop's own camera and mic.
# Each carries the `client_id` of the page that started the save; the hub sends them to it only.
ENROLL_STATE = "enroll.state"  # which screen: {session_id, phase, name, ...}
ENROLL_PREVIEW = "enroll.preview"  # live laptop-camera preview: {jpeg (bytes), face, hint}
ENROLL_LEVEL = "enroll.level"  # laptop-mic level meter: {level, db, hint, voiced_s, need_s}
ENROLL_MISMATCH = "enroll.mismatch"  # not the person the glasses saw: {score, threshold}
SESSION_FORGET = "session.forget"
PAUSED = "paused"
# The camera switched on or off from a page (`camera.set`): {on}
CAMERA_STATE = "camera.state"
COMMAND = "command"
STATUS_PART = "status.part"
# Engine-internal (Section 4): the aggregated status the hub sends to the console.
STATUS = "status"

TOPICS = frozenset(
    {
        VISION_FRAME,
        VISION_TRACKS,
        VISION_TRACK_LOST,
        VISION_APPEARANCE,
        VISION_DESCRIPTION,
        AUDIO_BLOCK,
        AUDIO_VAD,
        AUDIO_LEVEL,
        AUDIO_TRANSCRIPT,
        AUDIO_VOICE_MATCH,
        VOICE_HARVEST,
        CAPTION,
        CAPTION_RETRACT,
        CAPTION_TRANSLATION,
        SCENE,
        NAME_PROPOSAL,
        NAME_EVIDENCE,
        ALERT,
        REPLY_SUGGESTIONS,
        SENSORS_LEVELS,
        CAMERA_STATE,
        SENSORS_TOUCH,
        TOUCH_ACTION,
        HW_PATTERN,
        HW_STOP,
        HW_LINK,
        SPEECH_OUT_PLAYING,
        REPLY_SPOKEN,
        ENROLL_RESULT,
        ENROLL_PROGRESS,
        SAVE_REQUEST,
        SAVE_CANCEL,
        PERSON_CHANGED,
        ENROLL_STATE,
        ENROLL_PREVIEW,
        ENROLL_LEVEL,
        ENROLL_MISMATCH,
        SESSION_FORGET,
        PAUSED,
        COMMAND,
        STATUS_PART,
        STATUS,
    }
)

# ---------------------------------------------------------------------------
# WebSocket (contracts section 3 and "Pages integration additions")
# ---------------------------------------------------------------------------
ROLES = ("lens", "console", "phone")

WS_HELLO = "hello"
WS_WELCOME = "welcome"
WS_COMMAND = "command"
WS_SCENE = "scene"
WS_CAPTION = "caption"
WS_CAPTION_RETRACT = "caption_retract"
WS_NAME_PROPOSAL = "name_proposal"
WS_ALERT = "alert"
WS_REPLY_SUGGESTIONS = "reply_suggestions"
WS_REPLY_SPOKEN = "reply_spoken"
WS_STATUS = "status"
WS_PEOPLE = "people"
WS_THUMBNAILS = "thumbnails"
WS_EVENT_LOG = "event_log"
WS_LENS_SETTINGS = "lens_settings"
WS_PAUSED = "paused"
WS_ENROLL_RESULT = "enroll_result"
WS_PERSON_CHANGED = "person_changed"
WS_HW_LINK = "hw_link"
WS_CAMERA = "camera"
WS_ENROLL_PROGRESS = "enroll_progress"
WS_SAVE_REQUEST = "save_request"
WS_SAVE_CANCEL = "save_cancel"
# Enrollment station: only to the page that started the save (not in WS_AUDIENCE broadcasts)
WS_ENROLL_STATE = "enroll_state"
WS_ENROLL_PREVIEW = "enroll_preview"  # jpeg_b64 instead of the bus event's jpeg bytes
WS_ENROLL_LEVEL = "enroll_level"
WS_ENROLL_MISMATCH = "enroll_mismatch"
WS_STATION = {
    ENROLL_STATE: WS_ENROLL_STATE,
    ENROLL_PREVIEW: WS_ENROLL_PREVIEW,
    ENROLL_LEVEL: WS_ENROLL_LEVEL,
    ENROLL_MISMATCH: WS_ENROLL_MISMATCH,
}

_ALL = frozenset(ROLES)
# Which roles receive each JSON message type. Frames go to pages that asked for them.
WS_AUDIENCE: dict[str, frozenset[str]] = {
    WS_SCENE: frozenset({"lens", "console"}),
    WS_CAPTION: _ALL,
    WS_CAPTION_RETRACT: _ALL,
    WS_NAME_PROPOSAL: _ALL,
    WS_ALERT: _ALL,
    WS_REPLY_SUGGESTIONS: _ALL,
    WS_REPLY_SPOKEN: _ALL,
    WS_PAUSED: _ALL,
    WS_STATUS: frozenset({"console"}),
    WS_PEOPLE: frozenset({"console", "phone"}),
    WS_THUMBNAILS: frozenset({"console"}),
    WS_EVENT_LOG: frozenset({"console"}),
    WS_LENS_SETTINGS: frozenset({"lens", "phone"}),
    # the lens shows the save flow's result too (P-29), so enroll results go to every page
    WS_ENROLL_RESULT: _ALL,
    WS_PERSON_CHANGED: frozenset({"console", "phone"}),
    WS_HW_LINK: frozenset({"console", "phone"}),
    WS_CAMERA: _ALL,
    WS_ENROLL_PROGRESS: _ALL,
    WS_SAVE_REQUEST: _ALL,
    WS_SAVE_CANCEL: _ALL,
}

FRAME_HEADER_FORMAT = "<Qd"  # little-endian uint64 frame_no, float64 capture t
FRAME_HEADER_BYTES = 16

# ---------------------------------------------------------------------------
# Commands (contracts section 4)
# ---------------------------------------------------------------------------
COMMAND_NAMES = frozenset(
    {
        "enroll.start",
        "person.rename",
        "person.delete",
        "session.forget",
        "pause.toggle",
        "switch.set",
        "languages.set",
        "pattern.test",
        "calibrate.step",
        "speak",
        "name.answer",
        "alert.ack",
        "mark",
        "camera.set",
        "save.start",
        "save.cancel",
        "enroll.station",
    }
)
# `enroll.station` {action, ...}: start {name, consent, consent_t, request_id?, track_id?};
# retry / new_person / skip_voice / cancel {session_id}.
ENROLL_STATION_ACTIONS = ("start", "retry", "new_person", "skip_voice", "cancel")
# `enroll.state` phases, in the order a save usually goes through them.
ENROLL_PHASES = (
    "opening",
    "face",
    "mismatch",
    "face_failed",
    "saving",
    "voice",
    "voice_failed",
    "done",
    "cancelled",
    "fallback",
)

# Touch gestures (sensors.touch) and what the touch router makes of them (touch.action target).
GESTURES = ("tap", "hold", "double", "triple")
TOUCH_TARGETS = ("alert", "name", "save", "pause")
# Why a save request ended without an enrollment (save.cancel reason).
SAVE_CANCEL_REASONS = (
    "declined",
    "timeout",
    "lost",
    "replaced",
    "cancelled",
    "no_name",
    "already_saved",
)


def get(event: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dataclass-like object or a dict."""
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------
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
    mouth_open: float | None = None
    # Light-ASD speaking logit (V-22): > 0 talking in time with the sound; None = not scored
    asd_score: float | None = None


@dataclass
class Speaker:
    kind: str  # you, you_typed, face, probable_face, offscreen, someone
    track_id: int | None = None
    person_id: str | None = None
    label: str = ""
    side: str = "none"


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
class Command:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Bus events
# ---------------------------------------------------------------------------
@dataclass
class Frame:
    frame_no: int
    t: float
    image: Any  # BGR numpy array, full camera resolution


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
    crop: Any  # numpy; never sent to pages or logged


@dataclass
class Description:
    track_id: int
    label: str


@dataclass(frozen=True)
class AudioBlock:
    """Local-only mono PCM; t is the first sample on the shared clock."""

    t: float
    sample_rate: int
    samples: Any


@dataclass
class Vad:
    t: float
    is_speech: bool
    prob: float


@dataclass
class Transcript:
    utt_id: str
    t_start: float
    t_end: float
    text: str
    final: bool
    lang: str | None
    words: list[tuple[str, float, float]] = field(default_factory=list)


@dataclass
class VoiceMatch:
    utt_id: str
    person_id: str | None
    score: float


@dataclass
class VoiceHarvest:
    person_id: str
    t0: float
    t1: float
    talkers: int = 1  # most faces talking at once over [t0, t1] (A-21)


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
    """A caption segment id (`<utt_id>.<n>`) sent earlier is no longer part of its utterance."""

    utt_id: str


@dataclass
class CaptionTranslation:
    utt_id: str
    source_lang: str
    text_en: str


@dataclass
class Scene:
    frame_no: int
    t: float
    faces: list[FaceState]
    offscreen: list[Offscreen]
    you_speaking: bool


@dataclass
class NameProposal:
    proposal_id: str
    track_id: int
    name: str
    state: str  # proposed, confirmed, rejected, expired
    expires_t: float


@dataclass
class NameEvidence:
    track_id: int
    person_id: str | None
    name: str
    utt_id: str


@dataclass
class Alert:
    alert_id: str
    kind: str  # smoke, co, doorbell, knock
    side: str
    confidence: float
    state: str  # start, update, watch, acknowledged, clear


@dataclass
class ReplySuggestions:
    options: list[str]


@dataclass
class SensorLevels:
    t: float
    left: int
    right: int
    motor_on: bool


@dataclass
class SensorTouch:
    t: float
    gesture: str  # tap, hold, double, triple


@dataclass
class TouchAction:
    target: str  # alert, name, save, pause
    id: str | None
    accept: bool


@dataclass
class HwPattern:
    name: str  # T3, T4, BELL, NAME, OK, NO
    side: str  # L, R, B


@dataclass
class HwLink:
    connected: bool
    firmware: str | None = None
    driver: str | None = None


@dataclass
class SpeechOutPlaying:
    state: str  # start, end
    t: float


@dataclass
class ReplySpoken:
    text: str
    voice: str  # elevenlabs, kokoro
    t: float


@dataclass(frozen=True)
class EnrollResult:
    """Echo track_id so voice enrollment cannot reuse another person's consent."""

    person_id: str | None
    part: str  # face, voice
    ok: bool
    reason: str = ""
    track_id: int | None = None
    source: str = "glasses"  # "station": laptop save; "auto": conversation contact memory
    session_id: str | None = None  # the station save it belongs to


@dataclass(frozen=True)
class EnrollProgress:
    """How far a running enrollment is (0..1); `hint` is a short tip such as "more light"."""

    track_id: int | None
    part: str  # face, voice
    fraction: float
    person_id: str | None = None
    hint: str = ""
    source: str = "glasses"  # "station": laptop save; "auto": conversation contact memory
    session_id: str | None = None


@dataclass
class SaveRequest:
    """A double tap asked to save this person; the phone and console ask them for consent."""

    request_id: str
    track_id: int
    name: str
    t: float
    expires_t: float = 0.0
    person_id: str | None = None
    proposal_id: str | None = None


@dataclass
class SaveCancel:
    """A save request ended without an enrollment, or a double tap found nobody to save."""

    request_id: str | None
    reason: str  # see SAVE_CANCEL_REASONS
    track_id: int | None = None
    name: str = ""


@dataclass
class PersonChanged:
    person_id: str
    name: str
    action: str  # enrolled, renamed, deleted


@dataclass
class Paused:
    paused: bool


@dataclass
class StatusPart:
    part: str
    ok: bool
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class EnrollState:
    """Enrollment station: where a save at the laptop is up to (drives the phone's screens)."""

    session_id: str
    phase: str  # see ENROLL_PHASES
    name: str = ""
    client_id: int | None = None
    track_id: int | None = None
    request_id: str | None = None
    person_id: str | None = None
    face_ok: bool = False
    voice_ok: bool = False
    sentence: str = ""
    need_s: float = 5.0
    reason: str = ""


@dataclass
class EnrollPreview:
    """One live preview frame from the laptop camera, for the page that started the save."""

    session_id: str
    jpeg: bytes  # in memory only; the hub sends it as jpeg_b64
    width: int
    height: int
    face: list[float] | None = None  # [x, y, w, h] as fractions of the preview
    ok: bool = False
    hint: str = ""
    client_id: int | None = None


@dataclass
class EnrollLevel:
    """The laptop mic's level meter while the person reads the sentence."""

    session_id: str
    level: float  # 0..1 over -60..0 dBFS
    db: float
    hint: str = ""
    voiced_s: float = 0.0
    need_s: float = 5.0
    clipping: bool = False
    speech: bool = False
    peak: float = 0.0
    noise_db: float | None = None
    client_id: int | None = None


@dataclass
class EnrollMismatch:
    """The station face isn't the glasses face the save started from."""

    session_id: str
    score: float
    threshold: float
    name: str = ""
    track_id: int | None = None
    request_id: str | None = None
    client_id: int | None = None
