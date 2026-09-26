"""Webcam reader.

Section 1 - Vision. TODO: V-01. Plan: section 05 "Capture".

- Opens the webcam by name (the built-in camera is usually index 0), asks
  for MJPG 1920x1080 at 30 fps through Media Foundation, with a one-frame
  buffer so frames never queue up.
- Stamps every frame with the engine clock on arrival and keeps only the
  newest one.
- If the camera disappears, reports "camera lost" and keeps retrying every
  second; it's back within about 5 s of being replugged.
- A video file can stand in for the camera (tests and demos), played back at
  its own frame rate.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

# Media Foundation opens much faster with hardware transforms off; must be set before cv2 loads.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class CameraInfo:
    index: int
    backend: int
    name: str


def list_cameras() -> list[CameraInfo]:
    """Cameras with their names (Windows: Media Foundation order)."""
    try:
        from cv2_enumerate_cameras import enumerate_cameras
    except ImportError:
        return []
    backend = cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_ANY
    try:
        return [CameraInfo(c.index, c.backend, c.name) for c in enumerate_cameras(backend)]
    except Exception as exc:  # noqa: BLE001 - a driver error must not stop the engine
        log.warning("Could not list cameras: %s", exc)
        return []


def pick_camera(name: str, fallback_any: bool = True) -> CameraInfo | None:
    cams = list_cameras()
    for cam in cams:
        if name and name.lower() in cam.name.lower():
            return cam
    if fallback_any and cams:
        return cams[0]
    return None


class Camera:
    """Reads frames on its own thread and hands each one to `on_frame`."""

    def __init__(
        self,
        name: str = "Logitech",
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        source: str | int | None = None,
        fallback_any: bool = True,
        loop_file: bool = True,
        clock: Callable[[], float] = time.perf_counter,
        on_frame: Callable[[int, float, np.ndarray], None] | None = None,
        on_status: Callable[[bool, str], None] | None = None,
    ):
        self.name = name
        self.width, self.height, self.fps = width, height, fps
        self.source = source
        self.fallback_any = fallback_any
        self.loop_file = loop_file
        self.clock = clock
        self.on_frame = on_frame
        self.on_status = on_status
        self.device_name = ""
        self.connected = False
        self.frame_no = 0
        self.measured_fps = 0.0
        self._latest: tuple[int, float, np.ndarray] | None = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_file(self) -> bool:
        return isinstance(self.source, str) and os.path.isfile(self.source)

    # ---- lifecycle ----
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=2.0)

    def latest(self) -> tuple[int, float, np.ndarray] | None:
        return self._latest

    def wait_frame(self, after: int, timeout: float = 0.5) -> tuple[int, float, np.ndarray] | None:
        """Block until a frame newer than `after` arrives (or timeout)."""
        with self._cond:
            self._cond.wait_for(
                lambda: self._stop.is_set() or (self._latest and self._latest[0] > after), timeout
            )
        latest = self._latest
        return latest if latest and latest[0] > after else None

    # ---- internals ----
    def _open(self) -> cv2.VideoCapture | None:
        if self.is_file:
            cap = cv2.VideoCapture(self.source)
            self.device_name = os.path.basename(str(self.source))
            return cap if cap.isOpened() else None
        if isinstance(self.source, int):
            cap = cv2.VideoCapture(
                self.source, cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_ANY
            )
            self.device_name = f"camera {self.source}"
        else:
            info = pick_camera(self.name, self.fallback_any)
            if info is None:
                return None
            cap = cv2.VideoCapture(info.index, info.backend)
            self.device_name = info.name
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _set_status(self, ok: bool, detail: str) -> None:
        if ok != self.connected:
            self.connected = ok
            log.info("Camera %s: %s", "connected" if ok else "lost", detail)
            if self.on_status:
                self.on_status(ok, detail)

    def _run(self) -> None:
        while not self._stop.is_set():
            cap = self._open()
            if cap is None:
                self._set_status(False, "camera lost")
                self._stop.wait(1.0)
                continue
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._set_status(True, f"{self.device_name} {w}x{h}")
            self._read_loop(cap)
            cap.release()

    def _read_loop(self, cap: cv2.VideoCapture) -> None:
        file_period = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 30.0) if self.is_file else 0.0
        next_due = time.perf_counter()
        fails_since = None
        window_start, window_count = time.perf_counter(), 0
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                if self.is_file and self.loop_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                if self.is_file:
                    self._set_status(False, "end of file")
                    self._stop.wait(0.5)
                    return
                fails_since = fails_since or time.perf_counter()
                if time.perf_counter() - fails_since > 0.5:
                    self._set_status(False, "camera lost")
                    return
                self._stop.wait(0.01)
                continue
            fails_since = None
            if file_period:
                next_due += file_period
                delay = next_due - time.perf_counter()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    next_due = time.perf_counter()
            t = self.clock()
            self.frame_no += 1
            window_count += 1
            now = time.perf_counter()
            if now - window_start >= 1.0:
                self.measured_fps = window_count / (now - window_start)
                window_start, window_count = now, 0
            with self._cond:
                self._latest = (self.frame_no, t, frame)
                self._cond.notify_all()
            if self.on_frame:
                self.on_frame(self.frame_no, t, frame)
