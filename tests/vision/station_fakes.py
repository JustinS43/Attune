"""Fake devices and models for the enrollment station tests (V-23, A-21).

Frames and audio come faster than real time; the station paces itself on their own
timestamps, so a 5 s capture window takes well under a second here.
"""

from __future__ import annotations

import threading
import time

import numpy as np
from attune.vision.detector import Detection

W, H = 1280, 720


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def person(seed: int, dim: int = 512) -> np.ndarray:
    return unit(np.random.default_rng(seed).standard_normal(dim))


def face_det(cx: float = 0.5, cy: float = 0.45, width: float = 240.0) -> Detection:
    """A frontal face centred at (cx, cy) (fractions of the 1280x720 frame), `width` px wide."""
    x, y = cx * W, cy * H
    h = width * 1.25
    box = np.array([x - width / 2, y - h / 2, x + width / 2, y + h / 2], np.float32)
    kps = np.array(
        [
            [x - 0.2 * width, y - 0.1 * h],
            [x + 0.2 * width, y - 0.1 * h],
            [x, y + 0.05 * h],
            [x - 0.15 * width, y + 0.22 * h],
            [x + 0.15 * width, y + 0.22 * h],
        ],
        np.float32,
    )
    return Detection(box=box, score=0.9, kps=kps)


class FakeDetector:
    """Returns `plan(frame_index)` (a list of detections) for each frame it sees."""

    def __init__(self, plan=None):
        self.plan = plan or (lambda i: [face_det()])
        self.calls = 0

    def detect(self, image):
        i = self.calls
        self.calls += 1
        return list(self.plan(i))


class FakeEmbedder:
    """Prints close to `who` (a unit vector), with a little noise so they vary."""

    def __init__(self, who: np.ndarray, noise: float = 0.05, seed: int = 3, then=None):
        self.who = who
        self.noise = noise
        self.rng = np.random.default_rng(seed)
        self.then = then  # (n, other): after n prints, someone else is in front of the camera
        self.count = 0

    def embed(self, crops):
        out = []
        for _ in crops:
            if self.then is not None and self.count >= self.then[0]:
                self.who = self.then[1]
            self.count += 1
            v = self.who + self.noise * self.rng.standard_normal(self.who.shape).astype(np.float32)
            out.append(unit(v))
        return np.stack(out) if out else np.zeros((0, len(self.who)), np.float32)


def textured_frame(seed: int = 0) -> np.ndarray:
    """A lit, sharp-looking frame (the quality gate reads brightness and sharpness)."""
    rng = np.random.default_rng(seed)
    return rng.integers(60, 200, size=(H, W, 3), dtype=np.uint8)


class FakeCamera:
    """Frames at `fps` of media time, handed out as fast as they are asked for."""

    def __init__(
        self,
        frames=None,
        fps: float = 30.0,
        count: int = 100000,
        dead: bool = False,
        delay: float = 0.0,
    ):
        self.frames = frames or [textured_frame(0), textured_frame(1)]
        self.fps, self.count, self.dead = fps, count, dead
        self.delay = delay  # wall-clock seconds per frame (0: as fast as asked)
        self.label = "Fake laptop camera"
        self.started = self.stopped = False
        self.t0 = 1000.0
        self.events: list | None = None  # shared list to record the open/close order

    def start(self):
        self.started = True
        if self.events is not None:
            self.events.append("camera.start")

    def stop(self):
        self.stopped = True
        if self.events is not None:
            self.events.append("camera.stop")

    def wait_frame(self, after: int, timeout: float = 0.5):
        if self.dead or self.stopped or after + 1 > self.count:
            time.sleep(min(timeout, 0.01))
            return None
        if self.delay:
            time.sleep(self.delay)
        n = after + 1
        return n, self.t0 + n / self.fps, self.frames[n % len(self.frames)]


class FakeMic:
    """16 kHz blocks of `samples` (then silence), faster than real time."""

    def __init__(self, samples: np.ndarray, dead: bool = False):
        self.samples = np.asarray(samples, np.float32)
        self.dead = dead
        self.label = "Fake laptop mic"
        self.detail = ""
        self.i = 0
        self.t0 = 2000.0
        self.started = self.stopped = False
        self.events: list | None = None
        self._lock = threading.Lock()

    def start(self):
        self.started = True
        if self.events is not None:
            self.events.append("mic.start")

    def stop(self):
        self.stopped = True
        if self.events is not None:
            self.events.append("mic.stop")

    def read(self, timeout: float = 0.1):
        if self.dead or self.stopped:
            time.sleep(min(timeout, 0.01))
            return None
        with self._lock:
            i = self.i
            self.i += 160
        block = self.samples[i : i + 160]
        if len(block) < 160:
            block = np.zeros(160, np.float32)
        return self.t0 + i / 16000, block


def energy_vad(frame: np.ndarray) -> float:
    return 0.95 if float(np.sqrt(np.mean(frame * frame))) > 0.01 else 0.02


def speech_like(seconds: float, level: float = 0.1, seed: int = 0) -> np.ndarray:
    """Noise bursts in a syllable rhythm: loud enough to count as voiced."""
    rng = np.random.default_rng(seed)
    n = int(seconds * 16000)
    t = np.arange(n) / 16000
    env = (np.sin(2 * np.pi * 4 * t) > -0.6).astype(np.float32)
    return (level * env * rng.standard_normal(n)).astype(np.float32)
