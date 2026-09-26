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
from .camera import Camera
from .detector import FaceDetector
from .embedder import FaceEmbedder, align, crop_quality
from .enrollment import EnrollJob, finish, validate_request
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
        self.s, _ = load_settings(config)
        self.source = source
        self.root = root or os.getcwd()
        self.camera: Camera | None = None
        self.detector: FaceDetector | None = None
        self.embedder: FaceEmbedder | None = None
        self.mouth: MouthMeter | None = None
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
        log.info("Vision models loaded on %s", self.detector.providers[0])

    def connect(self) -> None:
        """Subscribe to the bus. Commands are queued and handled on the vision thread."""
        put = self._commands.put
        self.bus.subscribe(T.COMMAND, put)
        self.bus.subscribe(T.SESSION_FORGET, lambda ev: put({"name": "session.forget"}))
        self.bus.subscribe(
            T.PAUSED, lambda ev: put({"name": "_paused", "args": {"paused": T.get(ev, "paused")}})
        )
        self.bus.subscribe(T.NAME_PROPOSAL, lambda ev: put({"name": "_proposal", "args": ev}))

    def start(self) -> None:
        self.load_models()
        self.connect()
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
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vision", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.camera:
            self.camera.stop()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self.mouth:
            self.mouth.close()

    def _on_frame(self, frame_no: int, t: float, image: np.ndarray) -> None:
        self.bus.publish(T.VISION_FRAME, T.Frame(frame_no, t, image))

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
        elif name == "_proposal":
            self._on_proposal(args, now)

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

        t0 = time.perf_counter()
        self._lips(image, upd.active, t)
        self._timings["lips"].append(time.perf_counter() - t0)

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
        person = self.gallery.enroll(job.name, prints, job.consent_t)
        tr = self._find(job.track_id)
        if tr is not None:
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
        )

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
                },
            ),
        )
