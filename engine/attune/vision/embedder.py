"""Face prints: ArcFace ResNet50 (w600k_r50.onnx from buffalo_l) on ONNX Runtime.

Section 1 - Vision. TODO: V-05. Plan: section 05 "Faces".

A face is straightened with its five landmarks into the standard 112x112
ArcFace crop and turned into a 512-number face print (unit length, so the dot
product of two prints is their cosine similarity). Only good crops are used:
at least `min_crop_px` wide, roughly facing the camera, sharp and lit.
The buffalo_l pack's age/gender model is never loaded (privacy rule).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .detector import Detection
from .runtime import make_session

# Where the five landmarks sit in the standard 112x112 ArcFace crop.
ARCFACE_DST = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


@dataclass
class CropQuality:
    ok: bool
    reason: str  # "", small, turned, blurry, dark
    width: float
    yaw: float
    sharpness: float
    brightness: float


def align(image: np.ndarray, kps: np.ndarray) -> np.ndarray:
    """Straighten a face into the 112x112 ArcFace crop."""
    matrix, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), ARCFACE_DST, method=cv2.LMEDS)
    if matrix is None:
        matrix = cv2.getAffineTransform(kps[:3].astype(np.float32), ARCFACE_DST[:3])
    return cv2.warpAffine(image, matrix, (112, 112), borderValue=0.0)


def yaw_ratio(kps: np.ndarray) -> float:
    """How far the nose sits from the middle of the eyes, in eye distances (0 = facing us)."""
    left_eye, right_eye, nose = kps[0], kps[1], kps[2]
    eye_dist = float(np.linalg.norm(right_eye - left_eye)) or 1.0
    return float((nose[0] - (left_eye[0] + right_eye[0]) / 2) / eye_dist)


def crop_quality(
    det: Detection,
    crop: np.ndarray,
    min_px: int = 60,
    max_yaw: float = 0.35,
    min_sharpness: float = 25.0,
    min_brightness: float = 45.0,
) -> CropQuality:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    inner = gray[16:104, 16:96]  # skip the black border the alignment can add
    sharpness = float(cv2.Laplacian(inner, cv2.CV_64F).var())
    brightness = float(inner.mean())
    yaw = yaw_ratio(det.kps)
    width = det.width
    reason = ""
    if width < min_px:
        reason = "small"
    elif abs(yaw) > max_yaw:
        reason = "turned"
    elif brightness < min_brightness:
        reason = "dark"
    elif sharpness < min_sharpness:
        reason = "blurry"
    return CropQuality(reason == "", reason, width, yaw, sharpness, brightness)


class FaceEmbedder:
    def __init__(self, model_path: str, use_gpu: bool = True):
        self.session = make_session(model_path, use_gpu)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def embed(self, crops: list[np.ndarray]) -> np.ndarray:
        """Face prints for aligned 112x112 BGR crops, shape (n, 512), unit length."""
        if not crops:
            return np.zeros((0, 512), dtype=np.float32)
        blob = cv2.dnn.blobFromImages(
            crops, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True
        )
        feats = self.session.run([self.output_name], {self.input_name: blob})[0]
        norms = np.linalg.norm(feats, axis=1, keepdims=True)
        return (feats / np.maximum(norms, 1e-6)).astype(np.float32)
