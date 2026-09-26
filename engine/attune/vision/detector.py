"""Face finder: InsightFace SCRFD-10G (det_10g.onnx) on ONNX Runtime.

Section 1 - Vision. TODO: V-03. Plan: section 05 "Faces".

The frame is scaled so its long side equals `det_size` and padded only up to
the next multiple of 32 (960x544 for a 16:9 frame, not a 960x960 square, which
nearly halves the work), then SCRFD's three strides (8, 16, 32) are decoded into boxes, scores and five
landmarks (eyes, nose, mouth corners). Faces narrower than `min_face_px` are
dropped. The decoding follows InsightFace's MIT-licensed scrfd.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from .runtime import make_session

log = logging.getLogger(__name__)

_STRIDES = (8, 16, 32)
_ANCHORS_PER_CELL = 2


@dataclass
class Detection:
    box: np.ndarray  # x1, y1, x2, y2 in frame pixels
    score: float
    kps: np.ndarray  # (5, 2): left eye, right eye, nose, left mouth, right mouth

    @property
    def width(self) -> float:
        return float(self.box[2] - self.box[0])


class FaceDetector:
    def __init__(
        self,
        model_path: str,
        det_size: int = 960,
        score: float = 0.3,
        nms: float = 0.4,
        min_face_px: int = 36,
        use_gpu: bool = True,
    ):
        self.session = make_session(model_path, use_gpu)
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        if len(self.output_names) != 9:
            raise ValueError(
                f"Expected SCRFD with landmarks (9 outputs), got {len(self.output_names)}"
            )
        self.det_size = det_size
        self.score = score
        self.nms = nms
        self.min_face_px = min_face_px
        self._anchor_cache: dict[tuple[int, int], np.ndarray] = {}

    @property
    def providers(self) -> list[str]:
        return self.session.get_providers()

    def _anchors(self, height: int, width: int, stride: int) -> np.ndarray:
        key = (height, width, stride)
        if key not in self._anchor_cache:
            ys, xs = np.mgrid[: height // stride, : width // stride]
            centers = np.stack([xs, ys], axis=-1).reshape(-1, 2).astype(np.float32) * stride
            self._anchor_cache[key] = np.repeat(centers, _ANCHORS_PER_CELL, axis=0)
        return self._anchor_cache[key]

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Find faces in a BGR image. Returns detections sorted by score, best first."""
        h, w = image.shape[:2]
        scale = self.det_size / max(h, w)
        nw, nh = round(w * scale), round(h * scale)
        pw, ph = -(-nw // 32) * 32, -(-nh // 32) * 32
        canvas = np.zeros((ph, pw, 3), dtype=np.uint8)
        canvas[:nh, :nw] = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 128.0, (pw, ph), (127.5, 127.5, 127.5), swapRB=True
        )
        outs = self.session.run(self.output_names, {self.input_name: blob})

        all_scores, all_boxes, all_kps = [], [], []
        for i, stride in enumerate(_STRIDES):
            scores = outs[i].reshape(-1)
            boxes = outs[i + 3].reshape(-1, 4) * stride
            kps = outs[i + 6].reshape(-1, 10) * stride
            keep = np.where(scores >= self.score)[0]
            if keep.size == 0:
                continue
            anchors = self._anchors(ph, pw, stride)[keep]
            b = boxes[keep]
            k = kps[keep].reshape(-1, 5, 2)
            all_scores.append(scores[keep])
            all_boxes.append(np.concatenate([anchors - b[:, :2], anchors + b[:, 2:]], axis=1))
            all_kps.append(anchors[:, None, :] + k)
        if not all_scores:
            return []

        scores = np.concatenate(all_scores)
        boxes = np.concatenate(all_boxes) / scale
        kps = np.concatenate(all_kps) / scale
        xywh = np.concatenate([boxes[:, :2], boxes[:, 2:] - boxes[:, :2]], axis=1)
        keep = cv2.dnn.NMSBoxes(xywh.tolist(), scores.tolist(), self.score, self.nms)
        keep = np.array(keep).reshape(-1)

        dets = []
        for j in keep[np.argsort(-scores[keep])]:
            box = boxes[j].copy()
            box[[0, 2]] = box[[0, 2]].clip(0, w - 1)
            box[[1, 3]] = box[[1, 3]].clip(0, h - 1)
            det = Detection(box=box, score=float(scores[j]), kps=kps[j])
            if det.width >= self.min_face_px:
                dets.append(det)
        return dets
