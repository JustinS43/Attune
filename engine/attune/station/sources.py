"""Where the enrollment station's frames and audio come from.

Section 1 - Vision. TODO: V-23 (camera), A-21 (mic).

Cameras (all expose `start()`, `stop()`, `wait_frame(after, timeout)` and `label`):
- `DeviceCamera`: the laptop camera by name, through `vision.camera.Camera` (its hot-plug
  handling and small-mode retry), with `fallback_any=False` so it never opens another camera,
  `exclusive=True` so the main camera's fallback leaves it alone while the station holds it,
  and infrared cameras excluded by name.
- `SharedCamera`: frames handed over by the main camera when it already holds the laptop
  camera (the glasses webcam is missing and vision fell back to it): no second open.
- A video file (`[enroll] camera_source`) stands in for the camera in tests and demos.

Mics (all expose `start()`, `stop()`, `read(timeout)` -> (t, 16 kHz float32 block) or None):
- `DeviceMic`: the laptop mic by name through `audio.mic.MicReader` (WASAPI, COM init, the
  PortAudio stream guard, reopen after a loss), but with no fallback: if the named mic is
  missing it reports that instead of recording from another mic (the glasses mic).
- `FileMic`: a WAV file played in real time (tests and demos).

Frames and audio live in memory only and are dropped as soon as they are used.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from ..vision.camera import Camera, is_infrared  # IR (Windows Hello) cameras: never

log = logging.getLogger(__name__)

__all__ = ["DeviceCamera", "DeviceMic", "FileMic", "SharedCamera", "is_infrared"]


def _avoided(name: str, avoid: tuple[str, ...]) -> bool:
    """A device the glasses own (their camera or mic): the station never opens it."""
    name = (name or "").casefold()
    return any(a and a.casefold() in name for a in avoid)


# ------------------------------------------------------------------ cameras
class DeviceCamera:
    """The laptop camera, opened by name for the length of one save."""

    def __init__(
        self,
        name: str,
        width: int,
        height: int,
        fps: int,
        clock: Callable[[], float],
        source: str | None = None,
        avoid: tuple[str, ...] = (),
    ) -> None:
        self.camera = Camera(
            name,
            width,
            height,
            fps,
            source=source or None,
            fallback_any=False,
            loop_file=True,
            clock=clock,
            exclusive=True,
            exclude=lambda cam: is_infrared(cam.name) or _avoided(cam.name, avoid),
        )

    @property
    def label(self) -> str:
        return self.camera.device_name or self.camera.name

    @property
    def connected(self) -> bool:
        return self.camera.connected

    def start(self) -> None:
        self.camera.start()

    def stop(self) -> None:
        self.camera.stop()

    def wait_frame(self, after: int, timeout: float = 0.5):
        return self.camera.wait_frame(after, timeout)


class SharedCamera:
    """Frames from the main camera, which already holds the laptop camera."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.connected = True
        self._latest: tuple[int, float, np.ndarray] | None = None
        self._cond = threading.Condition()
        self._stopped = False

    def offer(self, frame_no: int, t: float, image: np.ndarray) -> None:
        """Called on the main camera's thread for every frame; keeps only the newest."""
        with self._cond:
            self._latest = (frame_no, t, image)
            self._cond.notify_all()

    def start(self) -> None:
        self._stopped = False

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._latest = None
            self._cond.notify_all()

    def wait_frame(self, after: int, timeout: float = 0.5):
        with self._cond:
            self._cond.wait_for(
                lambda: self._stopped or (self._latest is not None and self._latest[0] > after),
                timeout,
            )
            latest = self._latest
        return latest if latest is not None and latest[0] > after else None


# ------------------------------------------------------------------ mics
class _Blocks:
    """A small queue of 16 kHz blocks; the oldest are dropped if the reader falls behind."""

    def __init__(self, maxsize: int = 400) -> None:
        self.queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, t: float, samples: np.ndarray) -> None:
        try:
            self.queue.put_nowait((t, samples))
        except queue.Full:
            self.dropped += 1

    def read(self, timeout: float = 0.1):
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return


def _mic_reader_class():
    from ..audio.mic import MicReader

    class NamedOnlyMic(MicReader):
        """MicReader that opens only the named WASAPI mic and never falls back."""

        avoid: tuple[str, ...] = ()

        def _device(self) -> tuple[int | None, bool]:
            name = self.config["device_name"].casefold()
            devices, hosts = self.sd.query_devices(), self.sd.query_hostapis()
            for i, device in enumerate(devices):
                host = hosts[device["hostapi"]]["name"]
                if (
                    name
                    and device["max_input_channels"]
                    and name in device["name"].casefold()
                    and not _avoided(device["name"], self.avoid)
                    and (sys.platform != "win32" or "WASAPI" in host)
                ):
                    self.device_label = device["name"]
                    return i, True
            raise RuntimeError(f"no input device named like {self.config['device_name']!r}")

    return NamedOnlyMic


class DeviceMic:
    """The laptop mic, opened by name for the voice step only."""

    def __init__(
        self, name: str, clock: Callable[[], float], sd: Any = None, avoid: tuple[str, ...] = ()
    ) -> None:
        self.name = name
        self.blocks = _Blocks()
        cfg = {"device_name": name, "sample_rate": 48000, "block_ms": 10, "max_utterance_s": 30}
        self.reader = _mic_reader_class()(cfg, clock, self._deliver, sd=sd)
        self.reader.avoid = tuple(avoid)

    @property
    def label(self) -> str:
        return self.reader.device_label or self.name

    @property
    def connected(self) -> bool:
        return self.reader.connected

    @property
    def detail(self) -> str:
        return self.reader.detail

    def _deliver(self, block: dict) -> None:
        if block["sample_rate"] == 16000:
            self.blocks.put(float(block["t"]), np.asarray(block["samples"], np.float32))

    def start(self) -> None:
        self.reader.start()

    def stop(self) -> None:
        self.reader.stop()
        self.blocks.clear()

    def read(self, timeout: float = 0.1):
        return self.blocks.read(timeout)


class FileMic:
    """A WAV file as the laptop mic, in real time (or `speed` times faster for tests)."""

    BLOCK = 160  # 10 ms at 16 kHz

    def __init__(
        self,
        path: str,
        clock: Callable[[], float],
        speed: float = 1.0,
        lead_s: float = 0.0,
        samples: np.ndarray | None = None,
    ) -> None:
        if samples is None:
            from ..replay.player import read_wav, resample

            pcm, rate = read_wav(path)
            samples = resample(pcm, rate, 16000)
        self.samples = np.asarray(samples, np.float32)
        self.label = path or "test audio"
        self.clock, self.speed, self.lead_s = clock, max(1e-3, float(speed)), lead_s
        self.connected = False
        self.detail = "not started"
        self.blocks = _Blocks(maxsize=100000)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="station-file-mic", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self.blocks.clear()
        self.connected = False

    def read(self, timeout: float = 0.1):
        return self.blocks.read(timeout)

    def _run(self) -> None:
        self.connected, self.detail = True, "file"
        t0 = self.clock()
        start = time.perf_counter()
        lead = np.zeros(round(self.lead_s * 16000), np.float32)
        audio = np.concatenate([lead, self.samples])
        for i in range(0, len(audio), self.BLOCK):
            due = start + (i / 16000) / self.speed
            if self._stop.wait(max(0.0, due - time.perf_counter())):
                return
            self.blocks.put(t0 + i / 16000, audio[i : i + self.BLOCK])
        # after the file: silence, like a quiet room, until the station closes the mic
        i = len(audio)
        while not self._stop.is_set():
            due = start + (i / 16000) / self.speed
            if self._stop.wait(max(0.0, due - time.perf_counter())):
                return
            self.blocks.put(t0 + i / 16000, np.zeros(self.BLOCK, np.float32))
            i += self.BLOCK
