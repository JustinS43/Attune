"""Lip-motion score per face: MediaPipe Face Landmarker on face crops.

Section 1 - Vision. TODO: V-08. Plan: section 05 "Who's talking".

MediaPipe's own face finder only works within about 1 m, so it runs on a
square crop around each SCRFD box instead of the whole frame.

mouth-open ratio = gap between the inner lips (landmarks 13, 14)
                   / mouth width (landmarks 78, 308)
lip score        = standard deviation of that ratio over the last 1 s

Verified on the film clips: talking faces score 0.021-0.062, still faces
0.005-0.015, so talking is >= 0.03 and 0.015-0.03 is "uncertain".
"""

from __future__ import annotations

import atexit
import threading
from collections import deque

import cv2
import numpy as np

_UPPER, _LOWER, _LEFT, _RIGHT = 13, 14, 78, 308
_CROP = 256

# MediaPipe 1.0 deadlocks if a FaceLandmarker is garbage-collected while another one is
# running (its __del__ takes a dispatcher lock the running call already holds). So every
# landmarker stays referenced here until it's closed explicitly, or at exit.
_LIVE: set = set()
_LIVE_LOCK = threading.Lock()


@atexit.register
def _close_all() -> None:
    with _LIVE_LOCK:
        live = list(_LIVE)
        _LIVE.clear()
    for lm in live:
        try:
            lm.close()
        except Exception:  # noqa: BLE001, S110 - shutting down; nothing useful to do
            pass


def mouth_ratio(points: np.ndarray) -> float:
    """Mouth-open ratio from landmark pixel coordinates, shape (478, 2) or (468, 2)."""
    gap = float(np.linalg.norm(points[_UPPER] - points[_LOWER]))
    width = float(np.linalg.norm(points[_LEFT] - points[_RIGHT]))
    return gap / width if width > 1e-6 else 0.0


def face_crop(image: np.ndarray, box: np.ndarray, expand: float = 1.6) -> np.ndarray | None:
    """Square crop around a face box, enlarged so the landmarker sees the whole head."""
    h, w = image.shape[:2]
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    side = max(box[2] - box[0], box[3] - box[1]) * expand
    x1, y1 = round(cx - side / 2), round(cy - side / 2)
    x2, y2 = round(cx + side / 2), round(cy + side / 2)
    crop = image[max(y1, 0) : min(y2, h), max(x1, 0) : min(x2, w)]
    if crop.size == 0:
        return None
    pad = [max(-y1, 0), max(y2 - h, 0), max(-x1, 0), max(x2 - w, 0)]
    if any(pad):
        crop = cv2.copyMakeBorder(crop, *pad, cv2.BORDER_CONSTANT, value=0)
    return cv2.resize(
        crop, (_CROP, _CROP), interpolation=cv2.INTER_AREA if side > _CROP else cv2.INTER_LINEAR
    )


class MouthMeter:
    """Wraps the MediaPipe Face Landmarker (IMAGE mode, one face per crop)."""

    def __init__(self, model_path: str):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions, RunningMode

        self._mp = mp
        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.3,
            min_face_presence_confidence=0.3,
        )
        self._landmarker = FaceLandmarker.create_from_options(options)
        with _LIVE_LOCK:
            _LIVE.add(self._landmarker)

    def measure(self, image: np.ndarray, box: np.ndarray) -> float | None:
        """Mouth-open ratio for the face in `box`, or None if no landmarks were found."""
        crop = face_crop(image, box)
        if crop is None:
            return None
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        result = self._landmarker.detect(
            self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        )
        if not result.face_landmarks:
            return None
        pts = np.array(
            [(p.x * _CROP, p.y * _CROP) for p in result.face_landmarks[0]], dtype=np.float32
        )
        return mouth_ratio(pts)

    def close(self) -> None:
        with _LIVE_LOCK:
            if self._landmarker not in _LIVE:
                return
            _LIVE.discard(self._landmarker)
        self._landmarker.close()


class LipHistory:
    """Rolling mouth-open ratios for one track."""

    def __init__(self, window_s: float = 1.0, keep_s: float = 3.0):
        self.window_s = window_s
        self.keep_s = keep_s
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, t: float, ratio: float) -> None:
        self.samples.append((t, ratio))
        while self.samples and t - self.samples[0][0] > self.keep_s:
            self.samples.popleft()

    def score(self, now: float) -> float:
        recent = [r for t, r in self.samples if now - t <= self.window_s]
        if len(recent) < 5:
            return 0.0
        return float(np.std(recent))

    @property
    def latest(self) -> float | None:
        return self.samples[-1][1] if self.samples else None
