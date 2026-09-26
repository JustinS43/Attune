"""Face step of the enrollment station: face prints from the laptop camera.

Section 1 - Vision. TODO: V-23.

Every processed frame goes through the glasses' own pipeline pieces: SCRFD finds faces,
the largest one is the person being saved, it is aligned and passed through the same
quality gate (`crop_quality`: small, turned, dark, blurry), and ArcFace turns good crops
into prints (at most `face_rate_hz` a second). Extra checks for lining up with a laptop:
the face must sit in the middle of the preview (the phone's oval), be clearly the largest
face in view, and every print must stay close to the first ones (one person only).

The capture window (`face_s`) starts at the first good crop, so walking up to the laptop
doesn't count. The job then keeps the 8 most varied prints and needs at least 5, exactly
like the glasses (vision/enrollment.py). Nothing but the prints survives the step.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..vision.embedder import align, crop_quality
from ..vision.enrollment import REASONS, EnrollJob, finish, hint, progress
from . import preview


@dataclass
class FaceView:
    """What one processed frame showed: for the phone's preview and hint."""

    face: list[float] | None  # [x, y, w, h] as fractions of the preview crop, or None
    ok: bool  # the face is lined up and good enough right now
    hint: str
    accepted: bool  # a print was taken from this frame


class _Bucket:
    def __init__(self, rate: float) -> None:
        self.rate, self.tokens, self.t = rate, 1.0, None

    def take(self, now: float) -> bool:
        if self.t is not None:
            self.tokens = min(2.0, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class FaceStep:
    """Collects one person's face prints from station frames."""

    def __init__(
        self,
        settings,
        vision,
        detector,
        embedder,
        name: str,
        consent_t: str,
        track_id: int | None,
        start_t: float,
    ) -> None:
        self.s, self.v = settings, vision
        self.detector, self.embedder = detector, embedder
        # start_t stays infinite until the first good crop (progress() then reads 0)
        self.job = EnrollJob(-1 if track_id is None else int(track_id), name, consent_t, math.inf)
        self.opened_t = start_t
        self.bucket = _Bucket(settings.face_rate_hz)
        self.last_seen_t = -math.inf
        self.last_view: FaceView | None = None

    # ---------------------------------------------------------------- per frame
    def process(self, image: np.ndarray, t: float) -> FaceView:
        s, v, job = self.s, self.v, self.job
        h, w = image.shape[:2]
        crop = preview.portrait_box(w, h)
        dets = sorted(self.detector.detect(image), key=lambda d: -d.width)
        if not dets:
            reason = "lost" if t - self.last_seen_t < 1.5 and job.prints else "none"
            return self._reject(reason, t, None)
        det = dets[0]
        self.last_seen_t = t
        face = preview.to_preview(det.box, crop)
        if len(dets) > 1 and dets[1].width * s.dominant_ratio > det.width:
            return self._reject("crowd", t, face)
        cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
        if abs(cx - 0.5) > s.center_tol or abs(cy - 0.45) > s.center_tol + 0.1:
            return self._reject("off_center", t, face)
        if face[2] < s.min_face_share:
            return self._reject("small", t, face)
        if face[2] > s.max_face_share:
            return self._reject("close", t, face)
        aligned = align(image, det.kps)
        q = crop_quality(det, aligned, v.min_crop_px, v.max_yaw, v.min_sharpness, v.min_brightness)
        if not q.ok:
            return self._reject(q.reason, t, face)
        if not self.bucket.take(t):
            view = FaceView(face, True, hint(job, t), False)
            self.last_view = view
            return view
        emb = self.embedder.embed([aligned])[0]
        if len(job.prints) >= 2:
            ref = np.mean(job.prints[:3], axis=0)
            ref /= max(float(np.linalg.norm(ref)), 1e-6)
            if float(emb @ ref) < s.same_person:
                return self._reject("crowd", t, face)
        if not job.prints:
            job.start_t = t
        job.prints.append(emb)
        job.last_accept_t = t
        view = FaceView(face, True, "", True)
        self.last_view = view
        return view

    def _reject(self, reason: str, t: float, face: list[float] | None) -> FaceView:
        self.job.rejects[reason] += 1
        self.job.last_reject = (reason, t)
        view = FaceView(face, False, REASONS.get(reason, ""), False)
        self.last_view = view
        return view

    # ---------------------------------------------------------------- state
    @property
    def count(self) -> int:
        return len(self.job.prints)

    def progress(self, t: float) -> float:
        return progress(self.job, t, self.s.face_s, self.v.enroll_crops)

    def hint(self, t: float) -> str:
        return self.last_view.hint if self.last_view is not None and not self.last_view.ok else ""

    def done(self, t: float) -> bool:
        """Enough varied prints over the capture window (or a longer one with the minimum)."""
        elapsed = t - self.job.start_t
        n = len(self.job.prints)
        return (elapsed >= self.s.face_s and n >= self.v.enroll_crops) or (
            elapsed >= 2 * self.s.face_s and n >= self.v.enroll_min_crops
        )

    def timed_out(self, t: float) -> bool:
        return t - self.opened_t >= self.s.face_timeout_s

    def finish(self) -> tuple[np.ndarray | None, str]:
        """(the prints to store, "") or (None, the reason for the person)."""
        prints, reason = finish(self.job, self.v.enroll_min_crops, self.v.enroll_crops)
        if prints is None and not self.job.rejects:
            reason = REASONS["none"]
        return prints, reason
