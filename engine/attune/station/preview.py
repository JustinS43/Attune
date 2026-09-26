"""The live preview the phone shows while a face is saved at the laptop.

Section 1 - Vision. TODO: V-23.

A fixed 3:4 portrait crop from the middle of the laptop camera's frame, scaled to
`preview_width` (360x480 by default) and JPEG-encoded in memory. The phone draws an oval
guide over it; the person moves until their face sits in the oval. Crops are never stored.
"""

from __future__ import annotations

import cv2
import numpy as np


def portrait_box(width: int, height: int, aspect: float = 0.75) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the largest centred crop with w / h = aspect."""
    ch = height
    cw = round(ch * aspect)
    if cw > width:
        cw = width
        ch = round(cw / aspect)
    return (width - cw) // 2, (height - ch) // 2, cw, ch


def to_preview(box_xyxy, crop: tuple[int, int, int, int]) -> list[float]:
    """A face box (x1, y1, x2, y2 in frame pixels) as [x, y, w, h] fractions of the crop."""
    x0, y0, cw, ch = crop
    x1, y1, x2, y2 = (float(v) for v in box_xyxy)
    return [
        round((x1 - x0) / cw, 4),
        round((y1 - y0) / ch, 4),
        round((x2 - x1) / cw, 4),
        round((y2 - y1) / ch, 4),
    ]


def encode(
    image: np.ndarray, crop: tuple[int, int, int, int], out_width: int, quality: int
) -> bytes | None:
    """The crop scaled to out_width (keeping 3:4) as JPEG bytes, or None."""
    x0, y0, cw, ch = crop
    part = image[y0 : y0 + ch, x0 : x0 + cw]
    if part.size == 0:
        return None
    out_h = round(out_width * ch / cw)
    small = cv2.resize(part, (out_width, out_h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    return buf.tobytes() if ok else None
