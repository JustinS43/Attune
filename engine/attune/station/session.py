"""The enrollment station: save a person at the laptop's own camera and mic.

Section 1 - Vision (face) and 2 - Audio (voice), built by the enrollment stream.
TODO: V-23 (face), A-21 (voice), P-35 (phone screens). Contracts: docs/contracts.md
("Enrollment station").

The glasses camera and mic are for the world outside: captions, recognising people,
translation. Saving someone happens at the table, on the laptop:

1. The person being saved ticks consent on the phone, which sends `enroll.station`
   {action: start, name, consent: true, consent_t, request_id?, track_id?}. A save started
   from the glasses (a double tap on someone) carries that face's `track_id`; the phone's
   "Remember me" tab carries none.
2. Face: the laptop camera opens (or its frames are shared, if the main camera already
   holds it because the glasses webcam is missing). The phone that started the save gets
   a live preview (`enroll.preview`, a small JPEG ~12 times a second) with the face box and
   a hint, and `enroll.progress` {part: face}. Prints are collected like the glasses do
   (station/face.py). The camera closes as soon as the step ends: its light goes off.
3. Identity check: a save started from a glasses face compares the station prints with that
   face's prints. Too different: `enroll.mismatch`; the phone offers "Try again" (the face
   step again) or "Save as someone new" (no link to the glasses face). Nothing is saved
   until one of them is chosen.
4. The face prints are saved by the vision thread (gallery, `person.changed`, and every
   glasses track with this face is named at once).
5. Voice: the laptop mic opens; the person reads a sentence shown on the phone. The phone
   gets `enroll.level` (a meter and a hint) and `enroll.progress` {part: voice}. After
   `[voice] enroll_s` of voiced speech CAM++ makes the print, which is saved to voice.json
   and `enroll.result` {part: voice, source: station} tells the audio side to load it. The
   mic closes.

`enroll.state` tells the phone which screen to show. Frames, crops and audio exist only
in memory during the save, and the preview goes only to the page that started it.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..core import contracts as C
from ..vision.enrollment import identity_score, validate_request
from ..vision.settings import load_settings
from . import preview
from .face import FaceStep
from .settings import load_enroll_settings
from .sources import DeviceCamera, DeviceMic, FileMic, SharedCamera
from .voice import VoiceStep

log = logging.getLogger(__name__)

# enroll.state phases
OPENING = "opening"  # the laptop camera is starting
FACE = "face"  # collecting face prints (preview on the phone)
MISMATCH = "mismatch"  # not the person the glasses saw: waiting for Try again / Someone new
FACE_FAILED = "face_failed"  # not enough good face prints: waiting for Try again
SAVING = "saving"  # face prints are being saved
VOICE = "voice"  # reading the sentence (level meter on the phone)
VOICE_FAILED = "voice_failed"  # saved with their face; waiting for Try again / Finish
DONE = "done"  # saved (face, and voice unless skipped or failed)
CANCELLED = "cancelled"  # ended without saving anything (or the voice after the face)
FALLBACK = "fallback"  # the station can't run now; use the glasses instead

ACTIONS = ("start", "retry", "new_person", "skip_voice", "cancel")


@dataclass
class VisionHooks:
    """What the station needs from the running VisionService."""

    # (device name, delivering frames) of the main camera; ("", False) when it is off
    main_camera: Callable[[], tuple[str, bool]]
    # set (or clear, with None) a callback that gets every main-camera frame
    share_frames: Callable[[Callable[[int, float, np.ndarray], None] | None], None]
    # save face prints on the vision thread: dict in, {"person_id"} out (blocks until done)
    save_face: Callable[[dict], dict]


@dataclass
class Session:
    session_id: str
    client_id: int | None
    name: str
    consent_t: float
    request_id: str | None
    track_id: int | None
    glasses_prints: np.ndarray | None
    controls: queue.Queue = field(default_factory=queue.Queue)
    phase: str = OPENING
    person_id: str | None = None
    face_ok: bool = False
    voice_ok: bool = False
    linked: bool = True  # still tied to the glasses face (False after "Save as someone new")
    closed: threading.Event = field(default_factory=threading.Event)


class _Cancel(Exception):
    pass


class StationEnroller:
    """Runs one station save at a time, on its own thread."""

    def __init__(
        self,
        bus,
        config: dict[str, Any] | None,
        detector,
        embedder,
        hooks: VisionHooks,
        clock: Callable[[], float] = time.perf_counter,
        *,
        root: str | None = None,
        camera_factory: Callable[[], Any] | None = None,
        mic_factory: Callable[[], Any] | None = None,
        extractor: Callable[[np.ndarray], np.ndarray] | None = None,
        vad_factory: Callable[[], Callable[[np.ndarray], float]] | None = None,
    ) -> None:
        config = config or {}
        self.bus, self.clock, self.hooks = bus, clock, hooks
        self.s = load_enroll_settings(config)
        self.v, _ = load_settings(config)
        voice = config.get("voice") or {}
        self.need_s = float(voice.get("enroll_s", 5.0))
        self.voice_model = str(voice.get("model_path", "models/cam++.onnx"))
        self.voice_provider = str(voice.get("provider", "cpu"))
        self.root = Path(root or ".")
        engine = config.get("engine") or {}
        self.people_dir = self._path(str(Path(engine.get("data_dir", "data")) / "people"))
        # the glasses' own devices: the station never opens them
        self.avoid = tuple(
            x
            for x in (
                str((config.get("vision") or {}).get("camera_name", "")),
                str((config.get("audio") or {}).get("device_name", "")),
            )
            if x and x.casefold() not in (self.s.camera_name.casefold(), self.s.mic_name.casefold())
        )
        self.detector, self.embedder = detector, embedder
        self._camera_factory = camera_factory
        self._mic_factory = mic_factory
        self._extractor = extractor
        self._vad_factory = vad_factory
        self._vad = None
        self._models_lock = threading.Lock()
        self._lock = threading.Lock()
        self.session: Session | None = None
        self._thread: threading.Thread | None = None
        self._shared: SharedCamera | None = None

    def _path(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else self.root / path

    # ------------------------------------------------------------------ commands
    @property
    def enabled(self) -> bool:
        return self.s.source == "station"

    @property
    def active(self) -> bool:
        return self.session is not None and not self.session.closed.is_set()

    def command(self, args: dict, glasses_prints: np.ndarray | None = None) -> None:
        """An `enroll.station` command (from the vision thread; returns at once)."""
        action = args.get("action", "start")
        if action == "start":
            self._start(args, glasses_prints)
            return
        session = self.session
        if session is None or session.closed.is_set():
            return
        sid = args.get("session_id")
        if sid is not None and sid != session.session_id:
            return
        session.controls.put(action)

    def cancel(self, reason: str = "cancelled") -> None:
        """Pause, forget session or shutdown: end any running save now."""
        session = self.session
        if session is not None and not session.closed.is_set():
            session.controls.put(("cancel", reason))

    def stop(self) -> None:
        self.cancel("stopped")
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    def _start(self, args: dict, glasses_prints: np.ndarray | None) -> None:
        tid = args.get("track_id")
        tid = int(tid) if isinstance(tid, (int, float)) and not isinstance(tid, bool) else None
        name = args.get("name")
        consent_t = args.get("consent_t")
        err = validate_request(0 if tid is None else tid, name, args.get("consent"), consent_t)
        if not err and not isinstance(consent_t, (int, float)):
            err = "consent is required"
        session = Session(
            session_id=f"station-{uuid.uuid4().hex[:10]}",
            client_id=args.get("client_id"),
            name=(name or "").strip() if isinstance(name, str) else "",
            consent_t=float(consent_t) if isinstance(consent_t, (int, float)) else 0.0,
            request_id=args.get("request_id"),
            track_id=tid,
            glasses_prints=glasses_prints,
        )
        if err:
            session.closed.set()
            self._state(session, CANCELLED, reason=err)
            return
        if not self.enabled:
            session.closed.set()
            self._state(session, FALLBACK, reason="station_off")
            return
        with self._lock:
            old = self.session
            if old is not None and not old.closed.is_set():
                old.controls.put(("cancel", "replaced"))
            self.session = session
            thread = threading.Thread(
                target=self._run, args=(session, old), name="enroll-station", daemon=True
            )
            self._thread = thread
        thread.start()

    # ------------------------------------------------------------------ publishing
    def _state(self, session: Session, phase: str, **extra: Any) -> None:
        session.phase = phase
        body = {
            "session_id": session.session_id,
            "client_id": session.client_id,
            "phase": phase,
            "name": session.name,
            "track_id": session.track_id,
            "request_id": session.request_id,
            "person_id": session.person_id,
            "face_ok": session.face_ok,
            "voice_ok": session.voice_ok,
            "sentence": self.s.sentence,
            "need_s": self.need_s,
            "reason": "",
        }
        body.update(extra)
        self.bus.publish(C.ENROLL_STATE, body)

    def _progress(self, session: Session, part: str, fraction: float, hint: str) -> None:
        self.bus.publish(
            C.ENROLL_PROGRESS,
            {
                "track_id": session.track_id if session.linked else None,
                "part": part,
                "fraction": round(float(fraction), 3),
                "person_id": session.person_id,
                "hint": hint,
                "source": "station",
                "session_id": session.session_id,
            },
        )

    def _result(self, session: Session, part: str, ok: bool, reason: str = "") -> None:
        self.bus.publish(
            C.ENROLL_RESULT,
            {
                "person_id": session.person_id,
                "part": part,
                "ok": ok,
                "reason": reason,
                "track_id": session.track_id if session.linked else None,
                "source": "station",
                "session_id": session.session_id,
            },
        )

    # ------------------------------------------------------------------ the run
    def _run(self, session: Session, old: Session | None) -> None:
        if old is not None:
            old.closed.wait(3.0)  # let the previous save release the devices first
        log.info("Station: saving %s (session %s)", session.name, session.session_id)
        # load the voice models while the face step runs, so the voice step starts at once
        threading.Thread(target=self._preload, name="station-models", daemon=True).start()
        try:
            prints = self._face_until_accepted(session)
            self._save_face(session, prints)
            self._voice_until_done(session)
            self._state(session, DONE)
        except _Cancel as stop:
            reason = str(stop) or "cancelled"
            if session.face_ok:
                # the face is saved already; only the voice is left out
                self._state(session, DONE, reason=reason)
            else:
                self._state(session, CANCELLED, reason=reason)
        except _Fallback as fb:
            self._state(session, FALLBACK, reason=fb.reason, message=fb.message)
        except Exception:
            log.exception("Station save failed")
            self._state(session, CANCELLED if not session.face_ok else DONE, reason="error")
        finally:
            session.closed.set()
            log.info(
                "Station: %s ended (%s; face %s, voice %s)",
                session.session_id,
                session.phase,
                session.face_ok,
                session.voice_ok,
            )

    def _control(self, session: Session, timeout: float = 0.0):
        """The next control action, or None. A cancel raises _Cancel."""
        try:
            item = (
                session.controls.get(timeout=timeout) if timeout else session.controls.get_nowait()
            )
        except queue.Empty:
            return None
        if isinstance(item, tuple):
            action, reason = item
        else:
            action, reason = item, "declined"
        if action == "cancel":
            raise _Cancel(reason)
        return action

    def _wait_choice(self, session: Session, allowed: tuple[str, ...]) -> str:
        """Block for one of `allowed` (the phone's buttons); a timeout counts as cancel."""
        end = time.monotonic() + self.s.decision_timeout_s
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise _Cancel("timeout")
            action = self._control(session, min(left, 0.5))
            if action in allowed:
                return action

    # ------------------------------------------------------------------ face
    def _face_until_accepted(self, session: Session) -> np.ndarray:
        while True:
            prints, reason = self._face_step(session)
            if prints is None:
                self._result(session, "face", False, reason)
                self._state(session, FACE_FAILED, reason=reason)
                self._wait_choice(session, ("retry",))
                continue
            glasses = session.glasses_prints
            if session.linked and glasses is not None and len(glasses):
                score = identity_score(prints, glasses)
                log.info("Station: identity check %.2f (line %.2f)", score, self.s.identity_match)
                if score < self.s.identity_match:
                    self.bus.publish(
                        C.ENROLL_MISMATCH,
                        {
                            "session_id": session.session_id,
                            "client_id": session.client_id,
                            "request_id": session.request_id,
                            "track_id": session.track_id,
                            "name": session.name,
                            "score": round(score, 3),
                            "threshold": self.s.identity_match,
                        },
                    )
                    self._state(
                        session, MISMATCH, reason="not_the_same_person", score=round(score, 3)
                    )
                    choice = self._wait_choice(session, ("retry", "new_person"))
                    if choice == "retry":
                        continue
                    session.linked = False  # save as someone new: no link to the glasses face
            return prints

    def _open_camera(self, session: Session):
        """Share the main camera's frames if it holds the laptop camera, else open it."""
        if self._camera_factory is not None:
            return self._camera_factory(), False
        name, delivering = self.hooks.main_camera()
        if (
            not self.s.camera_source
            and delivering
            and self.s.camera_name.casefold() in (name or "").casefold()
        ):
            shared = SharedCamera(name)
            self.hooks.share_frames(shared.offer)
            self._shared = shared
            log.info("Station: sharing %s with the main camera", name)
            return shared, True
        return (
            DeviceCamera(
                self.s.camera_name,
                self.s.camera_width,
                self.s.camera_height,
                self.s.camera_fps,
                self.clock,
                source=self.s.camera_source or None,
                avoid=self.avoid,
            ),
            False,
        )

    def _close_camera(self, camera, shared: bool) -> None:
        if shared:
            self.hooks.share_frames(None)
            self._shared = None
        try:
            camera.stop()
        except Exception:
            log.exception("Station camera stop failed")

    def _face_step(self, session: Session) -> tuple[np.ndarray | None, str]:
        self._state(session, OPENING)
        camera, shared = self._open_camera(session)
        camera.start()
        try:
            frame = self._first_frame(session, camera)
            if frame is None:
                label = getattr(camera, "label", self.s.camera_name)
                raise _Fallback(
                    "camera_unavailable",
                    f"The laptop camera ({label}) didn't start. It may be busy in another app.",
                )
            s = self.s
            opened = frame[1]  # media time: frames may come faster than real time in tests
            step = FaceStep(
                s,
                self.v,
                self.detector,
                self.embedder,
                session.name,
                str(session.consent_t),
                session.track_id,
                opened,
            )
            self._state(session, FACE, camera=getattr(camera, "label", ""), shared=shared)
            last_no, last_progress, last_preview = frame[0] - 1, -1e9, -1e9
            period = 1.0 / max(s.preview_fps, 1.0)
            stale_since = None
            while True:
                self._control(session)  # a cancel raises
                item = camera.wait_frame(last_no, timeout=0.3)
                if item is None:
                    now = self.clock()
                    stale_since = stale_since or now
                    if now - stale_since > s.open_timeout_s:
                        raise _Fallback(
                            "camera_lost", "The laptop camera stopped sending pictures."
                        )
                    continue
                stale_since = None
                last_no, t, image = item
                if t - last_preview < period * 0.9:
                    continue
                last_preview = t
                view = step.process(image, t)
                self._preview(session, image, view)
                if t - last_progress >= 0.2:
                    last_progress = t
                    self._progress(session, "face", step.progress(t), view.hint)
                if step.done(t) or step.timed_out(t):
                    break
        finally:
            self._close_camera(camera, shared)  # the light goes off before anything else
        prints, reason = step.finish()
        if prints is not None:
            self._progress(session, "face", 1.0, "")
        return prints, reason

    def _first_frame(self, session: Session, camera):
        end = time.monotonic() + self.s.open_timeout_s
        while time.monotonic() < end:
            self._control(session)
            item = camera.wait_frame(0, timeout=0.3)
            if item is not None:
                return item
        return None

    def _preview(self, session: Session, image: np.ndarray, view) -> None:
        h, w = image.shape[:2]
        crop = preview.portrait_box(w, h)
        jpeg = preview.encode(image, crop, self.s.preview_width, self.s.preview_quality)
        if jpeg is None:
            return
        self.bus.publish(
            C.ENROLL_PREVIEW,
            {
                "session_id": session.session_id,
                "client_id": session.client_id,
                "jpeg": jpeg,
                "width": self.s.preview_width,
                "height": round(self.s.preview_width * crop[3] / crop[2]),
                "face": view.face,
                "ok": view.ok,
                "hint": view.hint,
            },
        )

    def _save_face(self, session: Session, prints: np.ndarray) -> None:
        self._control(session)
        self._state(session, SAVING)
        result = self.hooks.save_face(
            {
                "name": session.name,
                "prints": prints,
                "consent_t": str(session.consent_t),
                "track_id": session.track_id if session.linked else None,
                "session_id": session.session_id,
                "identity_match": self.s.identity_match,
            }
        )
        session.person_id = result["person_id"]
        session.face_ok = True

    # ------------------------------------------------------------------ voice
    def _voice_until_done(self, session: Session) -> None:
        while True:
            ok, reason = self._voice_step(session)
            if ok:
                return
            self._result(session, "voice", False, reason)
            self._state(session, VOICE_FAILED, reason=reason)
            if self._wait_choice(session, ("retry", "skip_voice")) == "skip_voice":
                raise _Cancel("voice_skipped")

    def _preload(self) -> None:
        try:
            self._models()
        except Exception as exc:  # noqa: BLE001 - the voice step reports it if it persists
            log.warning("Station voice models not loaded yet: %s", exc)

    def _models(self):
        """(Silero VAD, CAM++), loaded once on first use."""
        with self._models_lock:
            if self._extractor is None:
                from ..audio.voiceprint import CAMExtractor

                path = str(self._path(self.voice_model))
                self._extractor = CAMExtractor(path, self.voice_provider)
            if self._vad is None:
                if self._vad_factory is not None:
                    self._vad = self._vad_factory()
                else:
                    from ..audio.vad import SileroVAD

                    self._vad = SileroVAD()
            return self._vad, self._extractor

    def _open_mic(self):
        if self._mic_factory is not None:
            return self._mic_factory()
        if self.s.mic_source:
            return FileMic(self.s.mic_source, self.clock, lead_s=0.5)
        return DeviceMic(self.s.mic_name, self.clock, avoid=self.avoid)

    def _voice_step(self, session: Session) -> tuple[bool, str]:
        s = self.s
        vad, extract = self._models()  # loaded before the mic opens
        if hasattr(vad, "reset"):
            vad.reset()
        mic = self._open_mic()
        mic.start()
        try:
            quiet_since = self.clock()  # wall clock: no blocks at all means a dead mic
            step: VoiceStep | None = None  # made on the first block: paced by audio time
            self._state(session, VOICE, mic=getattr(mic, "label", s.mic_name))
            last_level = last_progress = -1e9
            level_period = 1.0 / max(s.level_hz, 1.0)
            while True:
                self._control(session)
                block = mic.read(timeout=0.1)
                if block is None:
                    if self.clock() - quiet_since > s.open_timeout_s:
                        label = getattr(mic, "label", s.mic_name)
                        detail = getattr(mic, "detail", "")
                        what = "didn't start" if step is None else "stopped"
                        return False, f"the laptop microphone ({label}) {what} {detail}".strip()
                    if step is None:
                        continue
                else:
                    quiet_since = self.clock()
                    if step is None:
                        step = VoiceStep(s, self.need_s, vad, float(block[0]))
                    step.feed(*block)
                now = step.now
                if now - last_level >= level_period:
                    last_level = now
                    self.bus.publish(
                        C.ENROLL_LEVEL,
                        {
                            "session_id": session.session_id,
                            "client_id": session.client_id,
                            **step.level(),
                            "hint": step.hint(),
                        },
                    )
                if now - last_progress >= 0.2:
                    last_progress = now
                    self._progress(session, "voice", step.progress(), step.hint())
                if step.done:
                    break
                if step.timed_out(now):
                    return False, "not enough speech"
        finally:
            try:
                mic.stop()  # closed before the print is even made
            except Exception:
                log.exception("Station mic stop failed")
        audio = step.audio()
        step.clear()
        try:
            vector = extract(audio)
        except ValueError:  # CAM++ found too little voice in it
            return False, "not enough speech"
        finally:
            del audio
        from ..audio.voiceprint import STATION, write_print

        write_print(self.people_dir, session.person_id, vector, session.consent_t, STATION)
        session.voice_ok = True
        self._progress(session, "voice", 1.0, "")
        self._result(session, "voice", True)
        return True, ""


class _Fallback(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(reason)
        self.reason, self.message = reason, message
