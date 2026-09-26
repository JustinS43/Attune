"""Instant colour label for strangers ("Person in blue").

Section 1 - Vision. TODO: V-12. Plan: section 05 "Describing strangers".

Once an unknown face has been steady for 1 s, look at the upper body below
it, name every pixel with one of the fixed colour words, and take the most
common one. No language model involved. Section 2 refines the garment from
the crop published in `vision.appearance`.

Only the colours in docs/contracts.md can come out.
"""

from __future__ import annotations

import cv2
import numpy as np

COLORS = [
    "black",
    "white",
    "grey",
    "navy",
    "blue",
    "green",
    "red",
    "orange",
    "yellow",
    "purple",
    "pink",
    "brown",
    "beige",
]


def upper_body_box(
    face_box: np.ndarray, frame_shape: tuple[int, ...]
) -> tuple[int, int, int, int] | None:
    """Region under the face where a shirt or jacket is, clipped to the frame."""
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = face_box
    fw, fh = x2 - x1, y2 - y1
    cx = (x1 + x2) / 2
    bx1, bx2 = int(max(cx - 0.9 * fw, 0)), int(min(cx + 0.9 * fw, w))
    by1, by2 = int(min(y2 + 0.35 * fh, h)), int(min(y2 + 1.8 * fh, h))
    if bx2 - bx1 < 16 or by2 - by1 < 16:
        return None
    return bx1, by1, bx2, by2


def classify_hsv(hsv: np.ndarray) -> np.ndarray:
    """Colour index (into COLORS) for each HSV pixel (OpenCV ranges: H 0-179, S and V 0-255)."""
    h = hsv[..., 0].astype(np.int32)
    s = hsv[..., 1].astype(np.int32)
    v = hsv[..., 2].astype(np.int32)
    out = np.full(h.shape, COLORS.index("grey"), dtype=np.int32)

    chroma = (s >= 45) & (v >= 50)
    hue = np.select(
        [h < 5, h < 20, h < 35, h < 85, h < 130, h < 150, h < 170],
        [COLORS.index(c) for c in ["red", "orange", "yellow", "green", "blue", "purple", "pink"]],
        default=COLORS.index("red"),
    )
    out = np.where(chroma, hue, out)
    # Named shades that depend on brightness and saturation.
    warm = (h < 25) | (h >= 170)
    out = np.where(chroma & warm & (h >= 5) & (h < 25) & (v < 150), COLORS.index("brown"), out)
    out = np.where(
        chroma & (h >= 8) & (h < 35) & (s < 110) & (v >= 150), COLORS.index("beige"), out
    )
    out = np.where(
        chroma & ((h < 8) | (h >= 160)) & (s < 140) & (v >= 170), COLORS.index("pink"), out
    )
    out = np.where(chroma & (h >= 100) & (h < 130) & (v < 110), COLORS.index("navy"), out)
    # Colourless pixels.
    grey = ~chroma
    out = np.where(grey & (v >= 200), COLORS.index("white"), out)
    out = np.where(grey & (v >= 60) & (v < 200), COLORS.index("grey"), out)
    out = np.where(v < 60, COLORS.index("black"), out)
    return out


def dominant_color(image: np.ndarray, region: tuple[int, int, int, int]) -> str | None:
    x1, y1, x2, y2 = region
    patch = image[y1:y2, x1:x2]
    if patch.size == 0:
        return None
    scale = min(1.0, 64.0 / max(patch.shape[:2]))
    if scale < 1.0:
        patch = cv2.resize(patch, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    patch = cv2.GaussianBlur(patch, (3, 3), 0)
    idx = classify_hsv(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV))
    counts = np.bincount(idx.reshape(-1), minlength=len(COLORS))
    return COLORS[int(np.argmax(counts))]
