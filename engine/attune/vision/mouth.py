"""Lip-motion score per face: MediaPipe Face Landmarker on face crops.

Section 1 - Vision. TODO: V-08. Plan: section 05 "Who's talking".

MediaPipe's own face finder only works within about 1 m, so it runs on a
square crop around each SCRFD box instead of the whole frame.

mouth-open ratio = gap between the inner lips (landmarks 13, 14)
                   / mouth width (landmarks 78, 308)
lip score        = how much that ratio moves at speech rates over the last 1 s:
                   the RMS of the ratio band-passed to about 2.5-8 Hz (V-19)

V-19: the old score (the plain standard deviation of the ratio) counted landmark
jitter and slow movements (lips parting, a yawn, a head turn) as talking. On a
live close-up (Brio on a table, ceiling light behind the head) a silent face
sat at 0.009-0.065, above the 0.03 talking line, so every sentence said off
camera went to it. Now:
- a 3-sample median drops single-frame landmark spikes and a 3-sample mean the
  jitter above about 8 Hz; subtracting a 0.4 s moving average drops everything
  slower than about 2.5 Hz (a yawn's slow sweep, drift as the head moves; lips
  that part and stay parted only count for the 0.3 s around the change).
  Syllables (about 3-6 Hz) pass.
- a mouth whose landmarks fall outside the camera image (the face half out of
  the frame) is not measured: MediaPipe guesses those points.
Measured with this score: the silent live face 0.000-0.007 while still (its
lips parting or a yawn still reach 0.02-0.06, which is why fusion also needs the
lips in time with the sound), film faces while talking mostly 0.01-0.045,
a steady 4 Hz talking sinusoid of +-0.12 about 0.07. See FusionSettings for the
lines drawn on it.
"""

from __future__ import annotations

import atexit
import threading
from collections import deque

import cv2
import numpy as np

_UPPER, _LOWER, _LEFT, _RIGHT = 13, 14, 78, 308
_MOUTH = (0, 13, 14, 17, 78, 308)  # outer and inner lip middles, and the corners
_CROP = 256
_EXPAND = 1.6
_RATE = 25.0  # Hz: ratios are resampled to this grid before filtering
_SLOW_S = 0.4  # the moving average subtracted as "slow" movement

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


def crop_square(box: np.ndarray, expand: float = _EXPAND) -> tuple[int, int, int, int]:
    """(x1, y1, x2, y2) of the square crop `face_crop` takes around a face box."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    side = max(box[2] - box[0], box[3] - box[1]) * expand
    return (
        round(cx - side / 2),
        round(cy - side / 2),
        round(cx + side / 2),
        round(cy + side / 2),
    )


def mouth_in_frame(
    points: np.ndarray, box: np.ndarray, shape: tuple[int, ...], margin: float = 0.02
) -> bool:
    """Whether the lip landmarks (crop pixels) lie inside the camera image, not its padding.

    When the lower face is out of the frame MediaPipe still returns a mouth, guessed
    from the rest of the face; its opening then swings or freezes regardless of speech.
    `margin` is a fraction of the crop's side.
    """
    x1, y1, x2, _ = crop_square(box)
    side = max(x2 - x1, 1)
    scale = side / _CROP
    h, w = shape[:2]
    pad = margin * side
    xy = points[list(_MOUTH)] * scale + (x1, y1)
    return bool(
        np.all(xy[:, 0] >= pad)
        and np.all(xy[:, 0] <= w - 1 - pad)
        and np.all(xy[:, 1] >= pad)
        and np.all(xy[:, 1] <= h - 1 - pad)
    )


def band_pass(values: np.ndarray, rate: float = _RATE, slow_s: float = _SLOW_S) -> np.ndarray:
    """Movement at speech rates (about 2.5-8 Hz at 25 Hz) of an evenly sampled series.

    3-sample median (drops one-frame spikes), then a 3-sample mean minus a `slow_s`
    moving average (drops slow drift). The result has the input's length.
    """
    v = np.asarray(values, dtype=np.float64)
    if len(v) < 3:
        return np.zeros_like(v)
    med = np.median(np.stack([np.r_[v[0], v[:-1]], v, np.r_[v[1:], v[-1]]]), axis=0)
    short = np.convolve(np.pad(med, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    n = max(round(slow_s * rate), 2)
    padded = np.pad(med, (n // 2, n - 1 - n // 2), mode="edge")
    slow = np.convolve(padded, np.ones(n) / n, mode="valid")
    return short - slow


def face_crop(image: np.ndarray, box: np.ndarray, expand: float = _EXPAND) -> np.ndarray | None:
    """Square crop around a face box, enlarged so the landmarker sees the whole head."""
    h, w = image.shape[:2]
    x1, y1, x2, y2 = crop_square(box, expand)
    side = x2 - x1
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
        """Mouth-open ratio for the face in `box`, or None if no landmarks were found
        or the mouth is outside the image."""
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
        if not mouth_in_frame(pts, box, image.shape):
            return None
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
        """RMS of the band-passed ratio over the last `window_s` (0 without recent samples)."""
        pts = [(t, r) for t, r in self.samples if now - self.window_s - _SLOW_S <= t <= now]
        if len(pts) < 5 or now - pts[-1][0] > 0.3:
            return 0.0  # too few measurements, or the mouth hasn't been seen lately
        ts, rs = np.array(pts, dtype=np.float64).T
        if ts[-1] - ts[0] < 0.5:
            return 0.0
        grid = np.arange(ts[0], now + 1e-9, 1.0 / _RATE)
        moved = band_pass(np.interp(grid, ts, rs))
        recent = moved[grid >= now - self.window_s]
        return float(np.sqrt(np.mean(recent * recent))) if len(recent) else 0.0

    @property
    def latest(self) -> float | None:
        return self.samples[-1][1] if self.samples else None
