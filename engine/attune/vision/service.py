"""VisionService: camera -> find -> track -> name -> lips -> colour.

Section 1 - Vision. TODO: V-02 (with V-01, V-03 to V-08, V-12).
Contracts: docs/contracts.md. Plan: section 05 "Faces", "Enrolling", "Describing strangers".

Threads:
- camera: reads frames and publishes every one as `vision.frame` (30/s).
- vision: processes the newest frame (older ones are skipped if it falls
  behind) and publishes `vision.tracks`, `vision.track_lost`,
  `vision.appearance`, `enroll.result`, `person.changed` and `status.part`.

Face prints are rate-limited to `max_rec_per_s` (10/s): new faces and
unknown faces first, then the 2-second rechecks of named faces. An
enrollment in progress gets its own budget.

Light-ASD (V-22, asd.py) runs on its own thread: the vision thread only adds each
face's mouth crop, the 16 kHz `audio.block` stream feeds it from the bus, and each
`Track` carries the face's latest `asd_score`.

The enrollment station (V-23, attune/station/) runs on its own thread with this service's
face finder and face printer. The vision thread hands it the command (with the glasses
face's prints, for the identity check) and later saves its prints into the gallery, naming
every glasses track with that face at once.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from . import types as T
from .appearance import dominant_color, upper_body_box
from .asd import ActiveSpeakerDetector, LightASD, asd_crop
from .camera import Camera
from .detector import FaceDetector
from .embedder import FaceEmbedder, align, crop_quality
from .enrollment import EnrollJob, finish, hint, identity_score, progress, validate_request
from .gallery import Gallery, Identity, IdentityRules
from .mouth import LipHistory, MouthMeter
from .settings import load_settings
from .tracker import FaceTrack, FaceTracker

log = logging.getLogger(__name__)


class TokenBucket:
    def __init__(self, rate: float, burst: float):
        self.rate, self.burst = rate, burst
        self.tokens, self.t = burst, None

    def take(self, now: float) -> bool:
        if self.t is not None:
            self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class VisionService:
    def __init__(
        self,
        bus,
        config: dict[str, Any] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        source: str | int | None = None,
        root: str | None = None,
    ):
        self.bus = bus
        self.clock = clock
        self.config = config or {}
        self.s, _ = load_settings(config)
        self.source = source
        self.root = root or os.getcwd()
        self.camera: Camera | None = None
        self.detector: FaceDetector | None = None
        self.embedder: FaceEmbedder | None = None
        self.mouth: MouthMeter | None = None
        self.asd: ActiveSpeakerDetector | None = None
        self.gallery = Gallery(self._path(self.s.people_dir))
        self.rules = IdentityRules(
            self.gallery,
            self.s.match_threshold,
            self.s.match_margin,
            self.s.match_hits,
            self.s.recheck_s,
            self.s.recheck_fails,
        )
        self.tracker = FaceTracker(
            self.s.det_score,
            self.s.track_survive_s,
            self.s.lost_list_s,
            self.s.edge_frac,
            self.s.reid_threshold,
        )
        self.paused = False
        self.camera_on = True
        self.job: EnrollJob | None = None
        self.station = None  # attune.station.session.StationEnroller, once models are loaded
        self._station_tap: Callable[[int, float, np.ndarray], None] | None = None
        self._request_prints: dict[str, tuple[float, np.ndarray]] = {}  # save.request snapshots
        self._commands: queue.Queue = queue.Queue()
        self._rec_budget = TokenBucket(self.s.max_rec_per_s, 5)
        self._enroll_budget = TokenBucket(8.0, 3)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._timings: dict[str, deque] = {
            k: deque(maxlen=60) for k in ("det", "rec", "lips", "total")
        }
        self._proc_times: deque = deque(maxlen=60)
        self._last_status = 0.0

    def _path(self, p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(self.root, p)

    # ---------------- lifecycle ----------------
    def load_models(self) -> None:
        s = self.s
        self.gallery.load()
        self.detector = FaceDetector(
            self._path(s.det_model), s.det_size, s.det_low_score, s.nms, s.min_face_px, s.use_gpu
        )
        self.embedder = FaceEmbedder(self._path(s.rec_model), s.use_gpu)
        try:
            self.mouth = MouthMeter(self._path(s.landmarker_model))
        except Exception as exc:  # noqa: BLE001 - lips are needed for who's talking, but faces still work without
            log.error("Lip motion disabled: %s", exc)
            self.mouth = None
        self.asd = self._load_asd()
        log.info("Vision models loaded on %s", self.detector.providers[0])

    def _load_asd(self) -> ActiveSpeakerDetector | None:
        """Light-ASD, or None (fusion then uses the lip score alone)."""
        s = self.s
        if not s.asd_enabled:
            return None
        path = self._path(s.asd_model)
        if not os.path.exists(path):
            log.info(
                "Light-ASD off: %s is missing (scripts/download_models.py light_asd)", s.asd_model
            )
            return None
        try:
            model = LightASD(path, s.asd_device)
        except Exception as exc:  # noqa: BLE001 - who's talking still works on the lip score
            log.error("Light-ASD disabled: %s", exc)
            return None
        log.info("Light-ASD loaded on %s", model.device)
        return ActiveSpeakerDetector(
            model,
            rate_hz=s.asd_rate_hz,
            window_s=s.asd_window_s,
            score_s=s.asd_score_s,
            max_gap_s=s.asd_max_gap_s,
            min_fps=s.asd_min_fps,
            av_offset_s=s.asd_av_offset_s,
            max_age_s=s.asd_max_age_s,
        )

    def connect(self) -> None:
        """Subscribe to the bus. Commands are queued and handled on the vision thread."""
        put = self._commands.put
        self.bus.subscribe(T.COMMAND, put)
        self.bus.subscribe(T.SESSION_FORGET, lambda ev: put({"name": "session.forget"}))
        self.bus.subscribe(
            T.PAUSED, lambda ev: put({"name": "_paused", "args": {"paused": T.get(ev, "paused")}})
        )
        self.bus.subscribe(T.NAME_PROPOSAL, lambda ev: put({"name": "_proposal", "args": ev}))
        self.bus.subscribe(T.AUDIO_BLOCK, self._on_audio_block)
        self.bus.subscribe(T.SAVE_REQUEST, lambda ev: put({"name": "_save_request", "args": ev}))

    def _on_audio_block(self, ev: Any) -> None:
        """16 kHz PCM for Light-ASD; just copied into its ring, so the bus isn't held up."""
        asd = self.asd
        if asd is not None and int(T.get(ev, "sample_rate", 0)) == 16000:
            asd.add_audio(float(T.get(ev, "t")), T.get(ev, "samples"))

    def start(self) -> None:
        self.load_models()
        self.connect()
        if self.asd is not None:
            self.asd.start()
        s = self.s
        self.camera = Camera(
            s.camera_name,
            s.width,
            s.height,
            s.fps,
            self.source,
            s.camera_fallback_any,
            clock=self.clock,
            on_frame=self._on_frame,
            on_status=self._on_camera_status,
        )
        self.camera.start()
        self._start_station()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vision", daemon=True)
        self._thread.start()

    def _start_station(self) -> None:
        """The enrollment station (V-23) shares this service's face models."""
        try:
            from ..station.session import StationEnroller, VisionHooks

            hooks = VisionHooks(self._main_camera, self._share_frames, self._station_save_wait)
            self.station = StationEnroller(
                self.bus,
                self.config,
                self.detector,
                self.embedder,
                hooks,
                self.clock,
                root=self.root,
            )
        except Exception as exc:  # noqa: BLE001 - saving at the glasses still works without it
            log.error("Enrollment station disabled: %s", exc)
            self.station = None

    def stop(self) -> None:
        self._stop.set()
        if self.station is not None:
            self.station.stop()
        if self.camera:
            self.camera.stop()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self.asd is not None:
            self.asd.stop()
        if self.mouth:
            self.mouth.close()

    def _on_frame(self, frame_no: int, t: float, image: np.ndarray) -> None:
        self.bus.publish(T.VISION_FRAME, T.Frame(frame_no, t, image))
        tap = self._station_tap
        if tap is not None:  # the station shares this camera (it is the laptop camera)
            tap(frame_no, t, image)

    def _on_camera_status(self, ok: bool, detail: str) -> None:
        self.bus.publish(T.STATUS_PART, T.StatusPart("camera", ok, detail))

    def _run(self) -> None:
        last = 0
        while not self._stop.is_set():
            item = self.camera.wait_frame(last, timeout=0.5)
            if item is None:
                self._drain_commands(self.clock())
                self._publish_status(self.clock(), None)
                continue
            last = item[0]
            try:
                self.process_frame(*item)
            except Exception:
                log.exception("Vision frame failed")

    # ---------------- commands ----------------
    def _drain_commands(self, now: float) -> None:
        """Handle queued commands, timed on the frame clock so replays behave like live runs."""
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle(T.get(cmd, "name"), T.get(cmd, "args") or {}, now)
            except Exception:
                log.exception("Command failed: %s", cmd)

    def _handle(self, name: str, args: Any, now: float) -> None:
        if name == "enroll.start":
            tid = T.get(args, "track_id")
            err = validate_request(
                tid, T.get(args, "name"), T.get(args, "consent"), T.get(args, "consent_t")
            )
            if not err and self._find(tid) is None:
                err = "that face isn't in view"
            if err:
                track = tid if isinstance(tid, int) else None
                self.bus.publish(T.ENROLL_RESULT, T.EnrollResult(None, "face", False, err, track))
                return
            self.job = EnrollJob(
                int(tid), T.get(args, "name").strip(), str(T.get(args, "consent_t")), now
            )
            log.info("Enrolling %s from track %s", self.job.name, tid)
            self._enroll_progress(now, force=True)
        elif name == "person.rename":
            person = self.gallery.rename(T.get(args, "person_id"), T.get(args, "name"))
            if person:
                self.bus.publish(
                    T.PERSON_CHANGED, T.PersonChanged(person.person_id, person.name, "renamed")
                )
        elif name == "person.delete":
            person = self.gallery.delete(T.get(args, "person_id"))
            if person:
                self._clear_identities({person.person_id})
                self.bus.publish(
                    T.PERSON_CHANGED, T.PersonChanged(person.person_id, person.name, "deleted")
                )
        elif name == "session.forget":
            if self.station is not None:
                self.station.cancel("cancelled")
            self._request_prints.clear()
            gone = set(self.gallery.forget_session())
            self._clear_identities(gone)
            self.tracker.lost.clear()
            for tr in self.tracker.active:
                tr.data.pop("color", None)
                tr.data["ident"].proposal = None
        elif name == "camera.set" and isinstance(T.get(args, "on"), bool):
            self._set_camera(bool(T.get(args, "on")))
        elif name == "_paused":
            self.paused = bool(T.get(args, "paused"))
            if self.paused and self.station is not None:
                self.station.cancel("paused")
        elif name == "_proposal":
            self._on_proposal(args, now)
        elif name == "enroll.station":
            self._station_command(dict(args) if isinstance(args, dict) else {}, now)
        elif name == "_save_request":
            self._snapshot_request(args, now)
        elif name == "_station_save":
            self._station_save(args, now)

    def _set_camera(self, on: bool) -> None:
        """Stop or restart the camera when the wearer turns it off or on from a page."""
        if self.camera is None or on == self.camera_on:
            return
        self.camera_on = on
        if on:
            log.info("Camera turned on")
            self.camera.start()
        else:
            log.info("Camera turned off")
            self.camera.stop()
            self.bus.publish(
                T.STATUS_PART, T.StatusPart("camera", True, "off (turned off by the wearer)")
            )

    def _on_proposal(self, ev: Any, now: float) -> None:
        tr = self._find(T.get(ev, "track_id"))
        if tr is None:
            return
        ident: Identity = tr.data["ident"]
        state = T.get(ev, "state")
        if state == "proposed":
            ident.proposal = (str(T.get(ev, "proposal_id")), T.get(ev, "name"))
        elif state == "confirmed":
            prints = list(tr.data.get("recent", []))
            if tr.embedding is not None and not prints:
                prints = [tr.embedding]
            if prints and ident.person_id is None:
                person = self.gallery.add_session(T.get(ev, "name"), np.stack(prints))
                self.rules.assign(ident, person.person_id, 1.0, now)
            ident.proposal = None
        else:  # rejected, expired
            ident.proposal = None

    # ---------------- enrollment station (V-23) ----------------
    def _main_camera(self) -> tuple[str, bool]:
        cam = self.camera
        if cam is None or not self.camera_on:
            return "", False
        return cam.device_name, bool(cam.connected)

    def _share_frames(self, tap) -> None:
        self._station_tap = tap

    def _track_any(self, track_id) -> FaceTrack | None:
        """An active track, or one on the lost list (it may have just turned away)."""
        if not isinstance(track_id, (int, float)) or isinstance(track_id, bool):
            return None
        tracks = self.tracker.active + self.tracker.lost
        return next((tr for tr in tracks if tr.track_id == int(track_id)), None)

    def _track_prints(self, tr: FaceTrack | None) -> list[np.ndarray]:
        """A glasses face's prints: its recent ones and its session person's."""
        if tr is None:
            return []
        rows = list(tr.data.get("recent", []))
        if tr.embedding is not None:
            rows.append(tr.embedding)
        ident = tr.data.get("ident")
        person = self.gallery.get(ident.person_id) if ident is not None else None
        if person is not None and not person.enrolled:
            rows.extend(person.prints)
        return rows

    def _snapshot_request(self, ev, now: float) -> None:
        """A double tap asked to save a face: keep its prints for the station's identity
        check, in case the face turns away before the person gives consent."""
        rid, tid = T.get(ev, "request_id"), T.get(ev, "track_id")
        for key in [k for k, (t, _) in self._request_prints.items() if now - t > 180.0]:
            del self._request_prints[key]
        rows = self._track_prints(self._track_any(tid))
        if rid and rows:
            self._request_prints[str(rid)] = (now, np.stack(rows))

    def _station_command(self, args: dict, now: float) -> None:
        if self.station is None:
            return
        glasses = None
        if args.get("action", "start") == "start":
            rows = self._track_prints(self._track_any(args.get("track_id")))
            snap = self._request_prints.pop(str(args.get("request_id")), None)
            if snap is not None:
                rows.extend(snap[1])
            glasses = np.stack(rows) if rows else None
        self.station.command(args, glasses)

    def _station_save_wait(self, req: dict, timeout: float = 10.0) -> dict:
        """Called on the station's thread: the gallery is changed on the vision thread."""
        done = threading.Event()
        box: dict = {}
        self._commands.put({"name": "_station_save", "args": {**req, "_done": done, "_box": box}})
        if not done.wait(timeout):
            raise TimeoutError("the vision thread didn't save the face in time")
        if "error" in box:
            raise box["error"]
        return box["result"]

    def _station_save(self, req: dict, now: float) -> None:
        box = req["_box"]
        try:
            box["result"] = self._station_enroll(req, now)
        except Exception as exc:  # noqa: BLE001 - handed back to the station thread
            box["error"] = exc
        finally:
            req["_done"].set()

    def _station_enroll(self, req: dict, now: float) -> dict:
        """Save a station enrollment's face prints, then name every glasses face that is them."""
        prints = np.asarray(req["prints"], dtype=np.float32)
        person = self.gallery.enroll(req["name"], prints, str(req["consent_t"]))
        tid = req.get("track_id")
        tr = self._track_any(tid)
        replaced: set[str] = set()
        if tr is not None:
            # the glasses face the save started from (its identity check passed): its session
            # entry is replaced, as in _finish_enrollment
            before = self.gallery.get(tr.data["ident"].person_id)
            if before is not None and not before.enrolled:
                self.gallery.delete(before.person_id)
                replaced.add(before.person_id)
            self.rules.assign(tr.data["ident"], person.person_id, 1.0, now)
        # any other session entry with this face would tie with the saved one on the match
        # margin, so neither would ever be named: the saved person supersedes it
        line = float(req.get("identity_match", 0.3))
        for other in list(self.gallery.people(enrolled_only=False)):
            if other.enrolled or other.person_id == person.person_id:
                continue
            if identity_score(prints, other.prints) >= line:
                self.gallery.delete(other.person_id)
                replaced.add(other.person_id)
        for tr2 in self.tracker.active + self.tracker.lost:
            ident = tr2.data.get("ident")
            if ident is not None and ident.person_id in replaced:
                self.rules.assign(ident, person.person_id, 1.0, now)
        self.bus.publish(
            T.ENROLL_RESULT,
            T.EnrollResult(
                person.person_id, "face", True, "", tid, "station", req.get("session_id")
            ),
        )
        self.bus.publish(
            T.PERSON_CHANGED, T.PersonChanged(person.person_id, person.name, "enrolled")
        )
        log.info(
            "Station: saved %s as %s with %d face prints%s",
            person.name,
            person.person_id,
            len(prints),
            f" (replaces {sorted(replaced)})" if replaced else "",
        )
        return {"person_id": person.person_id}

    def _clear_identities(self, person_ids: set[str]) -> None:
        for tr in self.tracker.active + self.tracker.lost:
            ident = tr.data.get("ident")
            if ident is not None and ident.person_id in person_ids:
                self.rules.clear(ident)

    def _find(self, track_id) -> FaceTrack | None:
        if track_id is None:
            return None
        return next((tr for tr in self.tracker.active if tr.track_id == int(track_id)), None)

    # ---------------- per frame ----------------
    def process_frame(self, frame_no: int, t: float, image: np.ndarray) -> T.Tracks:
        """Run the whole pipeline on one frame and publish the results."""
        t_start = time.perf_counter()
        self._drain_commands(t)
        if self.paused:
            out = T.Tracks(frame_no, t, [])
            self.bus.publish(T.VISION_TRACKS, out)
            return out

        t0 = time.perf_counter()
        dets = self.detector.detect(image)
        self._timings["det"].append(time.perf_counter() - t0)

        upd = self.tracker.update(dets, t, image.shape[1])
        for tr in upd.lost:
            self.bus.publish(T.VISION_TRACK_LOST, T.TrackLost(tr.track_id, t, tr.exit_side))
            if self.job and self.job.track_id == tr.track_id:
                self._finish_enrollment(t, lost=True)
        for tr in upd.active:
            tr.data.setdefault("ident", Identity())
            tr.data.setdefault("lips", LipHistory(self.s.lip_window_s))
            tr.data.setdefault("recent", deque(maxlen=8))

        t0 = time.perf_counter()
        self._face_prints(image, upd.active, t)
        self._timings["rec"].append(time.perf_counter() - t0)

        if self.job and t - self.job.start_t >= self.s.enroll_s:
            self._finish_enrollment(t)
        elif self.job:
            self._enroll_progress(t)

        t0 = time.perf_counter()
        self._lips(image, upd.active, t)
        self._timings["lips"].append(time.perf_counter() - t0)

        self._asd_crops(image, upd.active, t)
        self._colors(image, upd.active, t)

        out = T.Tracks(frame_no, t, [self._track_msg(tr, t) for tr in upd.active])
        self.bus.publish(T.VISION_TRACKS, out)
        self._timings["total"].append(time.perf_counter() - t_start)
        self._proc_times.append(time.perf_counter())
        self._publish_status(t, out)
        return out

    def _face_prints(self, image: np.ndarray, active: list[FaceTrack], t: float) -> None:
        s = self.s
        enroll_tid = self.job.track_id if self.job else None
        due: list[tuple[int, float, FaceTrack]] = []
        for tr in active:
            if not tr.seen:
                continue
            ident: Identity = tr.data["ident"]
            if not tr.reid_checked and t - tr.first_t > 1.0:
                tr.reid_checked = True  # no good crop in its first second: treat as a new person
            if tr.track_id == enroll_tid:
                due.append((0, 0.0, tr))
            elif not tr.reid_checked:
                due.append((1, -tr.det.width, tr))
            elif ident.person_id is None:
                if t - tr.data.get("last_emb_t", -1e9) >= 0.1:
                    due.append((2, -tr.det.width, tr))
            elif self.rules.needs_check(ident, t):
                due.append((3, -tr.det.width, tr))
        due.sort(key=lambda d: (d[0], d[1]))

        crops, owners = [], []
        for prio, _, tr in due:
            crop = align(image, tr.det.kps)
            q = crop_quality(
                tr.det, crop, s.min_crop_px, s.max_yaw, s.min_sharpness, s.min_brightness
            )
            if not q.ok:
                if prio == 0:
                    self.job.rejects[q.reason] += 1
                    self.job.last_reject = (q.reason, t)
                continue
            bucket = self._enroll_budget if prio == 0 else self._rec_budget
            if not bucket.take(t):
                continue
            crops.append(crop)
            owners.append((prio, tr))
        if not crops:
            return
        embs = self.embedder.embed(crops)
        for (prio, tr), emb in zip(owners, embs):
            tr.embedding = emb
            tr.data["last_emb_t"] = t
            if not tr.reid_checked:
                merged = self.tracker.reidentify(tr, emb)
                if merged is not None:
                    log.info("Track %d came back", tr.track_id)
            tr.data["recent"].append(emb)
            if prio == 0 and self.job:
                self.job.prints.append(emb)
                self.job.last_accept_t = t
            else:
                self.rules.observe(tr.data["ident"], emb, t)

    def _finish_enrollment(self, t: float, lost: bool = False) -> None:
        job, self.job = self.job, None
        if lost:
            job.rejects.clear()
        prints, reason = finish(job, self.s.enroll_min_crops, self.s.enroll_crops)
        if prints is None:
            self.bus.publish(
                T.ENROLL_RESULT, T.EnrollResult(None, "face", False, reason, job.track_id)
            )
            log.info(
                "Enrollment of %s refused: %s (%d good crops)", job.name, reason, len(job.prints)
            )
            return
        self._enroll_progress(t, force=True, job=job, fraction=1.0)
        person = self.gallery.enroll(job.name, prints, job.consent_t)
        tr = self._find(job.track_id)
        if tr is not None:
            # A confirmed "Sam?" named this face for the session first (P-29: the double tap
            # confirms, then saves). Saving them replaces that session entry: two gallery people
            # with the same face never clear the match margin, so neither would ever match.
            before = self.gallery.get(tr.data["ident"].person_id)
            if before is not None and not before.enrolled:
                self.gallery.delete(before.person_id)
                self._clear_identities({before.person_id})
            self.rules.assign(tr.data["ident"], person.person_id, 1.0, t)
        self.bus.publish(
            T.ENROLL_RESULT, T.EnrollResult(person.person_id, "face", True, "", job.track_id)
        )
        self.bus.publish(
            T.PERSON_CHANGED, T.PersonChanged(person.person_id, person.name, "enrolled")
        )
        log.info(
            "Enrolled %s as %s with %d face prints", person.name, person.person_id, len(prints)
        )

    def _enroll_progress(
        self,
        t: float,
        force: bool = False,
        job: EnrollJob | None = None,
        fraction: float | None = None,
    ) -> None:
        """`enroll.progress` for the face part, at most 5 times a second (P-29)."""
        job = job or self.job
        if job is None or (not force and t - job.last_progress_t < 0.2):
            return
        job.last_progress_t = t
        frac = (
            progress(job, t, self.s.enroll_s, self.s.enroll_crops) if fraction is None else fraction
        )
        self.bus.publish(
            T.ENROLL_PROGRESS,
            {
                "track_id": job.track_id,
                "person_id": None,
                "part": "face",
                "fraction": round(frac, 3),
                "hint": hint(job, t),
            },
        )

    def _lips(self, image: np.ndarray, active: list[FaceTrack], t: float) -> None:
        seen = sorted((tr for tr in active if tr.seen), key=lambda tr: -tr.det.width)
        for tr in active:
            tr.data["mouth"] = None
        if self.mouth is None:
            return
        for tr in seen[: self.s.lip_faces]:
            ratio = self.mouth.measure(image, tr.det.box)
            if ratio is not None:
                tr.data["lips"].add(t, ratio)
                tr.data["mouth"] = ratio

    def _asd_crops(self, image: np.ndarray, active: list[FaceTrack], t: float) -> None:
        """Hand the largest detected faces' mouth crops to Light-ASD (about 0.5 ms a face)."""
        if self.asd is None:
            return
        seen = sorted((tr for tr in active if tr.seen), key=lambda tr: -tr.det.width)
        for tr in seen[: self.s.asd_faces]:
            if tr.det.width < self.s.asd_min_face_px:
                continue
            crop = asd_crop(image, tr.kf.box)  # the filtered box: steadier crops
            if crop is not None:
                self.asd.add_face(tr.track_id, t, crop)
        self.asd.keep_only({tr.track_id for tr in active})

    def _colors(self, image: np.ndarray, active: list[FaceTrack], t: float) -> None:
        for tr in active:
            if not tr.seen or "color" in tr.data or tr.data["ident"].person_id is not None:
                continue
            if t - tr.steady_since < self.s.color_steady_s:
                continue
            region = upper_body_box(tr.det.box, image.shape)
            if region is None:
                continue
            color = dominant_color(image, region)
            if color:
                tr.data["color"] = color
                x1, y1, x2, y2 = region
                self.bus.publish(
                    T.VISION_APPEARANCE,
                    T.Appearance(tr.track_id, color, image[y1:y2, x1:x2].copy()),
                )

    def _track_msg(self, tr: FaceTrack, t: float) -> T.Track:
        ident: Identity = tr.data["ident"]
        person = self.gallery.get(ident.person_id)
        if person is not None:
            status = "enrolled" if person.enrolled else "named"
        elif ident.proposal is not None:
            status = "proposed"
        else:
            status = "unknown"
        x1, y1, x2, y2 = (float(v) for v in tr.box)
        return T.Track(
            track_id=tr.track_id,
            box=[x1, y1, x2 - x1, y2 - y1],
            face_px=round(x2 - x1),
            lip_score=tr.data["lips"].score(t),
            person_id=person.person_id if person else None,
            name=person.name if person else None,
            match_score=ident.match_score,
            status=status,
            mouth_open=tr.data.get("mouth"),
            asd_score=self._asd_score(tr.track_id, t),
        )

    def _asd_score(self, track_id: int, t: float) -> float | None:
        score = None if self.asd is None else self.asd.score(track_id, t)
        return None if score is None else round(score, 2)

    def _publish_status(self, t: float, out: T.Tracks | None) -> None:
        if t - self._last_status < 1.0:
            return
        self._last_status = t

        def ms(key):
            vals = self._timings[key]
            return round(1000 * sum(vals) / len(vals), 1) if vals else None

        times = self._proc_times
        proc_fps = (
            (len(times) - 1) / (times[-1] - times[0])
            if len(times) > 1 and times[-1] > times[0]
            else 0.0
        )
        cam = self.camera
        self.bus.publish(
            T.STATUS_PART,
            T.StatusPart(
                "vision",
                ok=bool(cam is None or cam.connected),
                detail="paused"
                if self.paused
                else "camera off"
                if not self.camera_on
                else ("" if cam is None or cam.connected else "camera lost"),
                metrics={
                    "camera": cam.device_name if cam else None,
                    "camera_fps": round(cam.measured_fps, 1) if cam else None,
                    "vision_fps": round(proc_fps, 1),
                    "det_ms": ms("det"),
                    "rec_ms": ms("rec"),
                    "lips_ms": ms("lips"),
                    "total_ms": ms("total"),
                    "faces": len(out.tracks) if out else 0,
                    "enrolled": len(self.gallery.people()),
                    "gpu": bool(
                        self.detector and "CUDAExecutionProvider" in self.detector.providers
                    ),
                    "asd": self.asd is not None and not self.asd.failed,
                    **(self.asd.metrics() if self.asd is not None else {}),
                },
            ),
        )
