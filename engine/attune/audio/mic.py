"""Microphone capture, continuous resampling, and timestamped in-memory audio."""

from __future__ import annotations

import logging
import math
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from . import portaudio

logger = logging.getLogger(__name__)


class AudioRing:
    """Keep a bounded mono stream; reject spans crossing capture gaps."""

    def __init__(self, seconds: float = 30, rate: int = 16000) -> None:
        if not math.isfinite(seconds) or seconds <= 0 or rate <= 0:
            raise ValueError("audio ring duration and rate must be positive")
        self.seconds, self.rate = seconds, rate
        self.capacity = int(seconds * rate)
        if self.capacity < 1:
            raise ValueError("audio ring must hold at least one sample")
        self.blocks: deque = deque()
        self.lock = threading.RLock()

    def append(self, t: float, samples: np.ndarray) -> None:
        """Retain only the configured duration, dropping ambiguous old timelines."""
        samples = np.asarray(samples, dtype=np.float32)
        if not math.isfinite(t) or samples.ndim != 1:
            raise ValueError("audio blocks require a finite timestamp and mono samples")
        if not len(samples):
            return
        with self.lock:
            if self.blocks:
                last_t, last_samples = self.blocks[-1]
                if t < last_t + len(last_samples) / self.rate - 0.5 / self.rate:
                    self.blocks.clear()
            end = t + len(samples) / self.rate
            if len(samples) > self.capacity:
                t += (len(samples) - self.capacity) / self.rate
                samples = samples[-self.capacity :]
            self.blocks.append((t, samples.copy()))
            cutoff = end - self.capacity / self.rate
            while self.blocks and self.blocks[0][0] + len(self.blocks[0][1]) / self.rate <= cutoff:
                self.blocks.popleft()
            if self.blocks and self.blocks[0][0] < cutoff:
                first_t, first_samples = self.blocks.popleft()
                trim = math.ceil((cutoff - first_t) * self.rate - 1e-6)
                if trim < len(first_samples):
                    self.blocks.appendleft(
                        (first_t + trim / self.rate, first_samples[trim:].copy())
                    )

    def span(self, start: float, end: float) -> np.ndarray:
        """Read an exact contiguous span, or raise if unavailable."""
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError("audio span requires finite increasing timestamps")
        with self.lock:
            parts, cursor = [], start
            for t, samples in self.blocks:
                a, b = max(start, t), min(end, t + len(samples) / self.rate)
                if b <= a:
                    continue
                if abs(a - cursor) > 1.5 / self.rate:
                    raise ValueError("audio span has a gap")
                parts.append(samples[round((a - t) * self.rate) : round((b - t) * self.rate)])
                cursor = b
            if end <= start or abs(cursor - end) > 1.5 / self.rate:
                raise ValueError("audio span is no longer available")
            return np.concatenate(parts).copy()

    def clear(self) -> None:
        with self.lock:
            self.blocks.clear()


class CaptureResampler:
    """Preserve sample time across chunk boundaries and expose capture gaps."""

    def __init__(self, sample_rate: int, gap_tolerance: float) -> None:
        import soxr

        self.sample_rate, self.gap_tolerance = sample_rate, gap_tolerance
        self.streams = {
            rate: soxr.ResampleStream(sample_rate, rate, 1, dtype="float32")
            for rate in (16000, 32000)
        }
        self.reset()

    def reset(self) -> None:
        """Discard filter history when the source stream loses continuity."""
        for stream in self.streams.values():
            stream.clear()
        self.origin: float | None = None
        self.previous_end: float | None = None
        self.produced = dict.fromkeys(self.streams, 0)

    def feed(self, t: float, samples: np.ndarray, overflow: bool = False) -> list[dict]:
        """Return available output with first-sample timestamps on the capture clock."""
        if overflow or (
            self.previous_end is not None and abs(t - self.previous_end) > self.gap_tolerance
        ):
            self.reset()
        self.previous_end = t + len(samples) / self.sample_rate
        if self.origin is None:
            self.origin = t
        blocks = []
        for rate, stream in self.streams.items():
            out = stream.resample_chunk(samples)
            if len(out):
                blocks.append(
                    {
                        "t": self.origin + self.produced[rate] / rate,
                        "sample_rate": rate,
                        "samples": out,
                    }
                )
                self.produced[rate] += len(out)
        return blocks


def _com_init() -> bool:
    """Initialize COM (multithreaded) for this thread; True if it must be released."""
    import ctypes

    return ctypes.windll.ole32.CoInitializeEx(None, 0) in (0, 1)  # S_OK, S_FALSE


_CAPTURE_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
_NAME_PROPS = (
    "{a45c254e-df1c-4efd-8020-67d146a850e0},2",  # endpoint description, e.g. "Microphone"
    "{b3f8fa53-0004-438e-9003-51a46e139bfc},6",  # interface name
    "{b3f8fa53-0004-438e-9003-51a46e139bfc},26",  # USB product name, e.g. "Brio 101"
)


def windows_input_present(name: str) -> bool | None:
    """Whether an active Windows capture endpoint matches ``name``; None if unknown.

    Reads the MMDevices registry, which Windows keeps current on hot-plug, unlike
    PortAudio's device list (refreshing that would close the stream in use).
    """
    if sys.platform != "win32" or not name:
        return None
    import winreg

    name = name.casefold()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CAPTURE_KEY) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                guid = winreg.EnumKey(root, i)
                try:
                    with winreg.OpenKey(root, guid) as key:
                        if winreg.QueryValueEx(key, "DeviceState")[0] != 1:  # ACTIVE
                            continue
                        with winreg.OpenKey(key, "Properties") as props:
                            for prop in _NAME_PROPS:
                                try:
                                    value = winreg.QueryValueEx(props, prop)[0]
                                except OSError:
                                    continue
                                if isinstance(value, str) and name in value.casefold():
                                    return True
                except OSError:
                    continue
    except OSError:
        return None
    return False


def _reopen_delay(failures: int) -> float:
    """0.5 s, 1 s, 2 s, then every 4 s while the microphone stays unavailable."""
    return min(0.5 * 2 ** max(failures - 1, 0), 4.0)


class MicReader:
    """Read mono PCM; fall back to the default input and return to the preferred mic.

    ``busy`` reports speech in progress, so a switch back to the preferred mic waits
    for a pause. ``present`` says whether the preferred mic is plugged in without
    touching PortAudio (default: the Windows endpoint registry).
    """

    RECHECK_S = 3.0  # how often to look for the preferred mic while on the fallback
    RECHECK_MAX_S = 60.0  # back-off when a sighting doesn't open

    def __init__(
        self,
        config: dict,
        clock: Callable[[], float],
        deliver: Callable[[dict], None],
        sd: Any = None,
        busy: Callable[[], bool] | None = None,
        present: Callable[[str], bool | None] | None = None,
    ) -> None:
        self.config, self.clock, self.deliver, self.sd = config, clock, deliver, sd
        self.busy = busy or (lambda: False)
        self.present = present or windows_input_present
        self.recheck_s = self.RECHECK_S
        self.device_label = ""
        self._logged_label: str | None = None
        self._switching = False
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.stream = None
        self.queue: queue.Queue = queue.Queue(maxsize=200)
        self.dropped = 0
        self.overflows = 0
        self.detail = "not started"
        self.connected = False
        self._clock_offset: float | None = None
        self.resampler: CaptureResampler | None = None

    def start(self) -> None:
        """Load device bindings and launch exactly one capture worker."""
        if self.thread and self.thread.is_alive():
            if self.stop_event.is_set():
                raise RuntimeError("previous microphone worker is still stopping")
            return
        if self.sd is None:
            import sounddevice

            self.sd = sounddevice
        self.resampler = CaptureResampler(
            self.config["sample_rate"], self.config["block_ms"] / 2000
        )
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="mic")
        self.thread.start()

    def _device(self) -> tuple[int | None, bool]:
        """Resolve the preferred mic by name, else the default input; (index, preferred)."""
        name = self.config["device_name"].casefold()
        devices, hosts = self.sd.query_devices(), self.sd.query_hostapis()
        for i, device in enumerate(devices):
            if (
                name
                and device["max_input_channels"]
                and name in device["name"].casefold()
                and (sys.platform != "win32" or "WASAPI" in hosts[device["hostapi"]]["name"])
            ):
                self.device_label = device["name"]
                return i, True
        # Passing None on Windows could select the default MME/DirectSound device.
        if sys.platform == "win32":
            for host in hosts:
                if "WASAPI" in host["name"]:
                    device = host["default_input_device"]
                    if device >= 0 and devices[device]["max_input_channels"]:
                        self.device_label = devices[device]["name"]
                        return device, False
            raise RuntimeError("no Windows WASAPI input device available")
        self.device_label = "default input"
        return None, False

    def _state(self, detail: str, error: Exception | None = None) -> None:
        """Set the health detail; log only when the state or device changes."""
        label = "" if error else self.device_label
        if (detail, label) == (self.detail, self._logged_label):
            return
        self.detail, self._logged_label = detail, label
        if error:
            logger.warning("%s: %s; retrying with a fresh device list", detail, error)
        else:
            logger.info("microphone: %s (%s)", detail, label)

    def _refresh_devices(self) -> None:
        """Re-initialise PortAudio so unplugged and replugged devices are seen."""
        if not portaudio.reinitialize(self.sd):
            logger.debug("PortAudio busy with playback; reusing its device list")

    def _preferred_back(self) -> bool:
        try:
            return bool(self.present(self.config["device_name"]))
        except Exception:  # a probe failure just means "not yet"
            logger.debug("preferred mic probe failed", exc_info=True)
            return False

    def _callback(self, data: np.ndarray, frames: int, timing: Any, status: Any) -> None:
        if self.stop_event.is_set():
            return
        if status:
            self.overflows += 1
        # Establish one mapping for this PortAudio stream. Repeatedly sampling the
        # Python clock would turn callback scheduling jitter into false PCM gaps.
        if self._clock_offset is None:
            self._clock_offset = self.clock() - timing.currentTime
        t = self._clock_offset + timing.inputBufferAdcTime
        try:
            self.queue.put_nowait((t, data[:, 0].copy(), bool(status)))
        except queue.Full:
            self.dropped += 1

    def health(self) -> dict:
        """Report capture availability independently of recognition worker health."""
        return {
            "ok": self.connected,
            "detail": self.detail,
            "metrics": {"capture_dropped": self.dropped, "capture_overflows": self.overflows},
        }

    def _drain(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def _run(self) -> None:
        # PortAudio's WASAPI host needs COM on the thread that opens the stream;
        # without it Pa_StartStream fails with an "unanticipated host error".
        com = sys.platform == "win32" and _com_init()
        try:
            self._capture()
        finally:
            if com:
                import ctypes

                ctypes.windll.ole32.CoUninitialize()

    def _capture(self) -> None:
        failures, refresh = 0, False
        while not self.stop_event.is_set():
            switch = False
            try:
                if refresh:
                    self._refresh_devices()
                with portaudio.stream_open():
                    device, preferred = self._device()
                    self.resampler.reset()
                    self._clock_offset = None
                    with self.sd.InputStream(
                        device=device,
                        samplerate=self.config["sample_rate"],
                        blocksize=round(
                            self.config["sample_rate"] * self.config["block_ms"] / 1000
                        ),
                        channels=1,
                        dtype="float32",
                        callback=self._callback,
                    ) as stream:
                        self.stream = stream
                        self.connected = True
                        self._state("preferred mic" if preferred else "fallback mic")
                        failures = 0
                        switch = self._pump(stream, preferred)
            except Exception as exc:  # noqa: BLE001 - any failure means reopen later
                if self.stop_event.is_set():
                    break
                failures += 1
                self._state("microphone unavailable", exc)
            finally:
                self.connected = False
                self.stream = None
                self._drain()
            # Every reopen after a loss or a switch needs PortAudio's current device list.
            refresh = True
            if not switch and not self.stop_event.is_set():
                self.stop_event.wait(_reopen_delay(failures))
        self.detail = "stopped"
        self.resampler.reset()

    def _pump(self, stream: Any, preferred: bool) -> bool:
        """Deliver audio until stopped; True to reopen on the returned preferred mic."""
        if preferred:
            self.recheck_s = self.RECHECK_S
        elif self._switching:
            # The last sighting didn't open as the preferred mic; look less often.
            self.recheck_s = min(self.recheck_s * 2, self.RECHECK_MAX_S)
        self._switching = False
        next_check = time.monotonic() + self.recheck_s
        wanted_since: float | None = None
        max_wait = float(self.config.get("max_utterance_s", 30))
        while not self.stop_event.is_set():
            try:
                t, samples, overflow = self.queue.get(timeout=0.2)
            except queue.Empty:
                if not stream.active:
                    raise RuntimeError("microphone disconnected") from None
            else:
                for block in self.resampler.feed(t, samples, overflow):
                    if self.stop_event.is_set():
                        break
                    self.deliver(block)
            if preferred:
                continue
            now = time.monotonic()
            if wanted_since is None and now >= next_check:
                next_check = now + self.recheck_s
                if self._preferred_back():
                    wanted_since = now
            # Switch in a pause; don't cut a sentence unless speech never stops.
            if wanted_since is not None and (not self.busy() or now - wanted_since > max_wait):
                logger.info("microphone: preferred mic is back; switching")
                self._switching = True
                return True
        return False

    def stop(self) -> None:
        """Stop accepting callbacks and release capture within the shutdown budget."""
        self.stop_event.set()
        stream = self.stream
        if stream is not None:
            stream.abort()
        if self.thread:
            self.thread.join(timeout=0.4)
