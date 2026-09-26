"""Shared fixtures for Section 1 tests.

Tests that need downloaded models or the LFW photos skip themselves when the
files aren't there (see models/README.md). LFW goes in data/testsets/lfw/.
"""

from __future__ import annotations

import collections
import os
import threading

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODELS = os.path.join(ROOT, "models", "faces")
DET_MODEL = os.path.join(MODELS, "buffalo_l", "det_10g.onnx")
REC_MODEL = os.path.join(MODELS, "buffalo_l", "w600k_r50.onnx")
LANDMARKER = os.path.join(MODELS, "mediapipe", "face_landmarker.task")
LFW = os.path.join(ROOT, "data", "testsets", "lfw")

needs_face_models = pytest.mark.skipif(
    not (os.path.isfile(DET_MODEL) and os.path.isfile(REC_MODEL)),
    reason="face models not downloaded",
)
needs_landmarker = pytest.mark.skipif(
    not os.path.isfile(LANDMARKER), reason="face landmarker not downloaded"
)
needs_lfw = pytest.mark.skipif(
    not os.path.isdir(LFW), reason="LFW test photos not downloaded"
)


class FakeBus:
    """Records everything published; delivers to subscribers synchronously."""

    def __init__(self):
        self.subs = collections.defaultdict(list)
        self.published = collections.defaultdict(list)
        self.lock = threading.Lock()

    def subscribe(self, topic, callback):
        self.subs[topic].append(callback)

    def publish(self, topic, event):
        with self.lock:
            self.published[topic].append(event)
        for cb in list(self.subs[topic]):
            cb(event)

    def last(self, topic):
        return self.published[topic][-1] if self.published[topic] else None


@pytest.fixture
def bus():
    return FakeBus()


def lfw_people(min_images: int = 2, limit: int | None = None) -> dict[str, list[str]]:
    """LFW people with at least `min_images` photos: name -> sorted image paths."""
    out = {}
    for name in sorted(os.listdir(LFW)):
        folder = os.path.join(LFW, name)
        if not os.path.isdir(folder):
            continue
        files = sorted(
            os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".jpg")
        )
        if len(files) >= min_images:
            out[name] = files
            if limit and len(out) >= limit:
                break
    return out
