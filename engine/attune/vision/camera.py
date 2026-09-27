"""Webcam reader.

Section 1 - Vision. TODO: V-01. Plan: section 05 "Capture".

- On macOS, prefers any USB webcam; elsewhere opens the webcam by name. Asks
  for MJPG 1920x1080 at 30 fps through Media Foundation, with a one-frame
  buffer so frames never queue up.
- Stamps every frame with the engine clock on arrival and keeps only the
  newest one.
- If the camera disappears, reports "camera lost" and keeps retrying every
  second; it's back within about 5 s of being replugged.
- If the named camera is missing it falls back to another one, and every few
  seconds looks for the named camera again; once it's back it switches to it.
- A video file can stand in for the camera (tests and demos), played back at
  its own frame rate.
- A camera opened with `exclusive=True` (the enrollment station, V-23) reserves its
  device while it holds it; other readers skip a reserved device when they fall back,
  so two readers never fight over one camera.
- Infrared cameras (Windows Hello) are never a fallback: they see no colour and no one's
  face as the other cameras do. Only a camera named for them opens one.
"""

from __future__ import annotations

import logging
import os
import re
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

_IR = re.compile(r"(\bIR\b|infrared)", re.IGNORECASE)


def is_infrared(name: str) -> bool:
    """A Windows Hello IR camera (by its device name)."""
    return bool(_IR.search(name or ""))


@dataclass
class CameraInfo:
    index: int
    backend: int
    name: str
    vid: int | None = None
    pid: int | None = None


def list_cameras() -> list[CameraInfo]:
    """Cameras with names and USB IDs (Windows: Media Foundation order)."""
    try:
        from cv2_enumerate_cameras import enumerate_cameras
    except ImportError:
        return []
    backend = cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_ANY
    try:
        return [
            CameraInfo(c.index, c.backend, c.name, getattr(c, "vid", None), getattr(c, "pid", None))
            for c in enumerate_cameras(backend)
        ]
    except Exception as exc:  # noqa: BLE001 - a driver error must not stop the engine
        log.warning("Could not list cameras: %s", exc)
        return []


_reserved: set[str] = set()  # device names an exclusive reader holds right now
_reserved_lock = threading.Lock()


def reserve(name: str) -> None:
    with _reserved_lock:
        _reserved.add(name)


def release(name: str) -> None:
    with _reserved_lock:
        _reserved.discard(name)


def reserved() -> frozenset[str]:
    with _reserved_lock:
        return frozenset(_reserved)


def pick_camera(
    name: str,
    fallback_any: bool = True,
    exclude: Callable[[CameraInfo], bool] | None = None,
) -> CameraInfo | None:
    cams = [cam for cam in list_cameras() if not (exclude and exclude(cam))]
    # A camera asked for by name wins on every platform; a reader that must not fall back
    # (the enrollment station) gets that camera or nothing.
    for cam in cams:
        if _matches(name, cam.name):
            return cam
    if not fallback_any:
        return None
    if sys.platform == "darwin":
        # USB VID/PID identifies external webcams without depending on their brand.
        external = [cam for cam in cams if _is_external(cam)]
        if external:
            return external[0]
    return cams[0] if cams else None


def _matches(name: str, cam_name: str) -> bool:
    return bool(name) and name.lower() in cam_name.lower()


def _is_external(cam: CameraInfo) -> bool:
    return cam.vid is not None and cam.pid is not None


def _is_preferred(cam: CameraInfo, name: str) -> bool:
    if _matches(name, cam.name):
        return True
    return _is_external(cam) if sys.platform == "darwin" else not name


class Camera:
    """Reads frames on its own thread and hands each one to `on_frame`."""

    PREFERRED_RECHECK_S = 3.0  # while on a fallback camera, look for the named one this often

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
        exclusive: bool = False,
        exclude: Callable[[CameraInfo], bool] | None = None,
    ):
        self.name = name
        self.exclusive = exclusive  # reserve the device while this reader holds it
        self.exclude = exclude  # devices this reader must never open
        self._holding: str | None = None  # the device name this reader has reserved
        self.width, self.height, self.fps = width, height, fps
        self.source = source
        self.fallback_any = fallback_any
        self.loop_file = loop_file
        self.clock = clock
        self.on_frame = on_frame
        self.on_status = on_status
        self.device_name = ""
        self.on_fallback = False
        self.connected = False
        self._detail = ""
        self.frame_no = 0
        self.measured_fps = 0.0
        self._reduced_mode = False
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
        self.on_fallback = False
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
            info = pick_camera(self.name, self.fallback_any, self._skip)
            if info is None:
                return None
            if self.exclusive:
                reserve(info.name)
                self._holding = info.name
            cap = cv2.VideoCapture(info.index, info.backend)
            self.device_name = info.name
            self.on_fallback = not _is_preferred(info, self.name)
        if not cap.isOpened():
            return None
        if not self._reduced_mode:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(
            cv2.CAP_PROP_FRAME_WIDTH, min(self.width, 1280) if self._reduced_mode else self.width
        )
        cap.set(
            cv2.CAP_PROP_FRAME_HEIGHT, min(self.height, 720) if self._reduced_mode else self.height
        )
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _skip(self, cam: CameraInfo) -> bool:
        """Never open an excluded device, one another reader has reserved, or an IR camera
        this reader didn't ask for by name."""
        if self.exclude is not None and self.exclude(cam):
            return True
        if is_infrared(cam.name) and not _matches(self.name, cam.name):
            return True
        return cam.name in reserved() and cam.name != self._holding

    def _release(self) -> None:
        if self._holding is not None:
            release(self._holding)
            self._holding = None

    def _set_status(self, ok: bool, detail: str) -> None:
        # A switch between cameras keeps ok=True but changes the device in the detail.
        if ok != self.connected or (ok and detail != self._detail):
            self.connected, self._detail = ok, detail
            log.info("Camera %s: %s", "connected" if ok else "lost", detail)
            if self.on_status:
                self.on_status(ok, detail)

    def _preferred_is_back(self) -> bool:
        return any(_is_preferred(cam, self.name) and not self._skip(cam) for cam in list_cameras())

    def _run(self) -> None:
        while not self._stop.is_set():
            cap = self._open()
            if cap is None:
                self._release()
                self._set_status(False, "camera lost")
                self._stop.wait(1.0)
                continue
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            try:
                self._read_loop(cap, f"{self.device_name} {w}x{h}")
            finally:
                cap.release()
                self._release()

    def _read_loop(self, cap: cv2.VideoCapture, detail: str) -> None:
        file_period = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 30.0) if self.is_file else 0.0
        next_due = time.perf_counter()
        fails_since = None
        window_start, window_count = time.perf_counter(), 0
        next_check = time.perf_counter() + self.PREFERRED_RECHECK_S
        while not self._stop.is_set():
            if self.on_fallback and time.perf_counter() >= next_check:
                next_check = time.perf_counter() + self.PREFERRED_RECHECK_S
                if self._preferred_is_back():
                    log.info("Camera %r is back; switching from %s", self.name, self.device_name)
                    return  # _run releases the fallback and reopens by name
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
                    if not self._reduced_mode:
                        self._reduced_mode = True
                        log.warning(
                            "Camera opened without usable frames; retrying at up to 1280x720"
                        )
                    return
                self._stop.wait(0.01)
                continue
            fails_since = None
            self._set_status(True, detail)
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
