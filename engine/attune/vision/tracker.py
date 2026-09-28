"""Face tracker, ByteTrack style.

Section 1 - Vision. TODO: V-04. Plan: section 05 "Faces".

- Each track has a constant-velocity Kalman filter on (centre x, centre y,
  aspect, height), so boxes are matched to where a face is heading.
- Matching is a cascade: faces seen in the last 0.2 s pick first, then
  staler tracks, so a leftover track can't steal a moving face. Within each
  step, boxes pair by overlap (IoU >= 0.3), then by distance for fast motion
  (centre within 0.8 face widths, similar size). Low-score detections then
  rescue tracks that are still unmatched (IoU >= 0.5).
- A track survives `survive_s` (1 s) without a detection, for someone turning
  away, then moves to a lost list for `lost_s` (10 s) together with the side
  it left on. A new face that matches a lost track's face print gets the old
  track back (`reidentify`), with its name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .detector import Detection


class KalmanBox:
    """Constant-velocity Kalman filter on (cx, cy, aspect, h); velocities are per second."""

    _POS_STD = 1.0 / 20
    _VEL_STD = 1.0 / 160

    def __init__(self, box: np.ndarray):
        self.mean = np.zeros(8)
        self.mean[:4] = _to_xyah(box)
        h = self.mean[3]
        std = np.array(
            [
                2 * self._POS_STD * h,
                2 * self._POS_STD * h,
                1e-2,
                2 * self._POS_STD * h,
                10 * self._VEL_STD * h,
                10 * self._VEL_STD * h,
                1e-5,
                10 * self._VEL_STD * h,
            ]
        )
        self.cov = np.diag(std**2)

    def predict(self, dt: float) -> None:
        dt = max(dt, 1e-3)
        f = np.eye(8)
        f[:4, 4:] = np.eye(4) * dt
        h = self.mean[3]
        frames = dt * 30.0  # noise tuned per frame at 30 fps
        std = np.array(
            [
                self._POS_STD * h,
                self._POS_STD * h,
                1e-2,
                self._POS_STD * h,
                self._VEL_STD * h * 30,
                self._VEL_STD * h * 30,
                1e-5,
                self._VEL_STD * h * 30,
            ]
        )
        q = np.diag(std**2) * frames
        self.mean = f @ self.mean
        self.cov = f @ self.cov @ f.T + q

    def update(self, box: np.ndarray) -> None:
        z = _to_xyah(box)
        h = self.mean[3]
        r = np.diag(np.array([self._POS_STD * h, self._POS_STD * h, 1e-1, self._POS_STD * h]) ** 2)
        hm = np.eye(4, 8)
        s = hm @ self.cov @ hm.T + r
        k = self.cov @ hm.T @ np.linalg.inv(s)
        self.mean = self.mean + k @ (z - hm @ self.mean)
        self.cov = (np.eye(8) - k @ hm) @ self.cov

    @property
    def box(self) -> np.ndarray:
        cx, cy, a, h = self.mean[:4]
        w = a * h
        return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])


def _to_xyah(box: np.ndarray) -> np.ndarray:
    w, h = box[2] - box[0], box[3] - box[1]
    return np.array([box[0] + w / 2, box[1] + h / 2, w / max(h, 1e-6), h])


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-6)


def distance_score(a: np.ndarray, b: np.ndarray, max_widths: float = 0.8) -> np.ndarray:
    """1 at the same centre, falling to 0 at `max_widths` face widths apart; 0 if sizes differ a lot."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    ca = (a[:, :2] + a[:, 2:]) / 2
    cb = (b[:, :2] + b[:, 2:]) / 2
    wa = (a[:, 2] - a[:, 0])[:, None]
    wb = (b[:, 2] - b[:, 0])[None, :]
    dist = np.linalg.norm(ca[:, None, :] - cb[None, :, :], axis=2) / np.maximum(wa, 1e-6)
    ratio = wb / np.maximum(wa, 1e-6)
    score = 1.0 - dist / max_widths
    score[(ratio < 0.7) | (ratio > 1.4)] = 0.0
    return np.clip(score, 0.0, None)


def greedy_match(iou: np.ndarray, threshold: float) -> list[tuple[int, int]]:
    """Pair rows and columns by highest IoU first (fine for a handful of faces)."""
    pairs = []
    if iou.size == 0:
        return pairs
    used_r, used_c = set(), set()
    for flat in np.argsort(-iou, axis=None):
        r, c = np.unravel_index(flat, iou.shape)
        if iou[r, c] < threshold:
            break
        if r in used_r or c in used_c:
            continue
        used_r.add(r)
        used_c.add(c)
        pairs.append((int(r), int(c)))
    return pairs


@dataclass
class FaceTrack:
    track_id: int
    kf: KalmanBox
    det: Detection
    first_t: float
    last_t: float
    predicted_t: float
    seen: bool = True  # detected in the latest frame
    hits: int = 1
    steady_since: float = 0.0  # start of the current unbroken run of detections
    embedding: np.ndarray | None = None  # latest good face print
    reid_checked: bool = False
    lost_t: float = 0.0
    exit_side: str = "none"
    data: dict[str, Any] = field(
        default_factory=dict
    )  # identity, lips, colour: owned by the service

    @property
    def box(self) -> np.ndarray:
        # Publish the filtered box too: matching already uses it, but raw detector
        # boxes made a visible caption jump on every frame.
        return self.kf.box


@dataclass
class TrackerUpdate:
    active: list[FaceTrack]
    new: list[FaceTrack]
    lost: list[FaceTrack]


class FaceTracker:
    def __init__(
        self,
        det_score: float = 0.5,
        survive_s: float = 1.0,
        lost_s: float = 10.0,
        edge_frac: float = 0.10,
        reid_threshold: float = 0.5,
    ):
        self.det_score = det_score
        self.survive_s = survive_s
        self.lost_s = lost_s
        self.edge_frac = edge_frac
        self.reid_threshold = reid_threshold
        self.active: list[FaceTrack] = []
        self.lost: list[FaceTrack] = []
        self._next_id = 1

    def reset(self) -> None:
        self.active.clear()
        self.lost.clear()

    def update(self, dets: list[Detection], t: float, frame_width: int) -> TrackerUpdate:
        for tr in self.active:
            tr.kf.predict(t - tr.predicted_t)
            tr.predicted_t = t
            tr.seen = False

        high = [d for d in dets if d.score >= self.det_score]
        low = [d for d in dets if d.score < self.det_score]
        pred = np.array([tr.kf.box for tr in self.active]).reshape(-1, 4)
        high_boxes = np.array([d.box for d in high]).reshape(-1, 4)

        unmatched_tracks = set(range(len(self.active)))
        unmatched_high = set(range(len(high)))
        recent = [i for i, tr in enumerate(self.active) if t - tr.last_t <= 0.2 + 1e-6]
        stale = [i for i, tr in enumerate(self.active) if t - tr.last_t > 0.2 + 1e-6]
        for tier in (recent, stale):
            for scorer, threshold in ((iou_matrix, 0.3), (distance_score, 0.0)):
                rows = [i for i in tier if i in unmatched_tracks]
                cols = sorted(unmatched_high)
                if not rows or not cols:
                    continue
                scores = scorer(pred[rows], high_boxes[cols])
                for r, c in greedy_match(scores, threshold if threshold else 1e-6):
                    self._assign(self.active[rows[r]], high[cols[c]], t)
                    unmatched_tracks.discard(rows[r])
                    unmatched_high.discard(cols[c])
        rest = sorted(unmatched_tracks)
        if rest and low:
            iou = iou_matrix(pred[rest], np.array([d.box for d in low]).reshape(-1, 4))
            for r, c in greedy_match(iou, 0.5):
                self._assign(self.active[rest[r]], low[c], t)
                unmatched_tracks.discard(rest[r])

        newly_lost = []
        for i in sorted(unmatched_tracks, reverse=True):
            tr = self.active[i]
            if t - tr.last_t > self.survive_s:
                self.active.pop(i)
                tr.lost_t = t
                tr.exit_side = self._exit_side(tr, frame_width)
                self.lost.append(tr)
                newly_lost.append(tr)
        self.lost = [tr for tr in self.lost if t - tr.lost_t <= self.lost_s]

        new = []
        for c in sorted(unmatched_high):
            tr = FaceTrack(self._next_id, KalmanBox(high[c].box), high[c], t, t, t, steady_since=t)
            self._next_id += 1
            self.active.append(tr)
            new.append(tr)
        return TrackerUpdate(list(self.active), new, newly_lost)

    def reidentify(self, track: FaceTrack, embedding: np.ndarray) -> FaceTrack | None:
        """Give a new track back a recently lost identity if the face prints match.

        Returns the lost track that was merged in, or None.
        """
        track.reid_checked = True
        best, best_sim = None, self.reid_threshold
        for old in self.lost:
            if old.embedding is None:
                continue
            sim = float(np.dot(old.embedding, embedding))
            if sim >= best_sim:
                best, best_sim = old, sim
        if best is None:
            return None
        self.lost.remove(best)
        track.track_id = best.track_id
        track.data = best.data
        track.first_t = best.first_t
        return best

    def _assign(self, tr: FaceTrack, det: Detection, t: float) -> None:
        if not tr.seen and t - tr.last_t > 0.2:
            tr.steady_since = t  # the unbroken run of detections starts again
        tr.kf.update(det.box)
        tr.det = det
        tr.last_t = t
        tr.seen = True
        tr.hits += 1

    def _exit_side(self, tr: FaceTrack, frame_width: int) -> str:
        """Which edge a lost face left by. Faces vanish from the detector while still half in
        view, so the last box is pushed 0.3 s further along the face's own motion first."""
        box = tr.det.box + np.array([1, 0, 1, 0]) * tr.kf.mean[4] * 0.3
        if box[0] <= self.edge_frac * frame_width:
            return "left"
        if box[2] >= (1 - self.edge_frac) * frame_width:
            return "right"
        return "none"
