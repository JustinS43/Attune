"""Microphone capture, continuous resampling, and timestamped in-memory audio."""

from __future__ import annotations

import logging
import queue
import sys
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class AudioRing:
    """Keep a bounded mono stream; reject spans crossing capture gaps."""

    def __init__(self, seconds: float = 30, rate: int = 16000) -> None:
        self.seconds, self.rate = seconds, rate
        self.blocks: deque = deque()
        self.lock = threading.RLock()

    def append(self, t: float, samples: np.ndarray) -> None:
        with self.lock:
            self.blocks.append((t, np.asarray(samples, dtype=np.float32).copy()))
            cutoff = t + len(samples) / self.rate - self.seconds
            while self.blocks and self.blocks[0][0] + len(self.blocks[0][1]) / self.rate <= cutoff:
                self.blocks.popleft()

    def span(self, start: float, end: float) -> np.ndarray:
        """Read an exact contiguous span, or raise if unavailable."""
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


class MicReader:
    """Read mono PCM; recover on the host's default input after device loss."""

    def __init__(
        self,
        config: dict,
        clock: Callable[[], float],
        deliver: Callable[[dict], None],
        sd: Any = None,
    ) -> None:
        self.config, self.clock, self.deliver, self.sd = config, clock, deliver, sd
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

    def _device(self, fallback: bool) -> int | None:
        name = self.config["device_name"].casefold()
        devices, hosts = self.sd.query_devices(), self.sd.query_hostapis()
        if not fallback:
            for i, device in enumerate(devices):
                if (
                    device["max_input_channels"]
                    and name in device["name"].casefold()
                    and (sys.platform != "win32" or "WASAPI" in hosts[device["hostapi"]]["name"])
                ):
                    return i
        # Passing None on Windows could select the default MME/DirectSound device.
        if sys.platform == "win32":
            for host in hosts:
                if "WASAPI" in host["name"]:
                    device = host["default_input_device"]
                    if device >= 0 and devices[device]["max_input_channels"]:
                        return device
            raise RuntimeError("no Windows WASAPI input device available")
        return None

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
        fallback = False
        while not self.stop_event.is_set():
            try:
                device = self._device(fallback)
                self.resampler.reset()
                self._clock_offset = None
                with self.sd.InputStream(
                    device=device,
                    samplerate=self.config["sample_rate"],
                    blocksize=round(self.config["sample_rate"] * self.config["block_ms"] / 1000),
                    channels=1,
                    dtype="float32",
                    callback=self._callback,
                ) as stream:
                    self.stream = stream
                    self.connected = True
                    self.detail = "fallback mic" if fallback or device is None else "preferred mic"
                    while not self.stop_event.is_set():
                        try:
                            t, samples, overflow = self.queue.get(timeout=0.2)
                        except queue.Empty:
                            if not stream.active:
                                raise RuntimeError("microphone disconnected")
                            continue
                        for block in self.resampler.feed(t, samples, overflow):
                            if self.stop_event.is_set():
                                break
                            self.deliver(block)
            except Exception:
                if not self.stop_event.is_set():
                    logger.exception("microphone unavailable; retrying default input")
                    self.detail, fallback = "microphone unavailable", True
            finally:
                self.connected = False
                self.stream = None
                self._drain()
            if not self.stop_event.is_set():
                self.stop_event.wait(0.5)
        self.detail = "stopped"
        self.resampler.reset()

    def stop(self) -> None:
        """Stop accepting callbacks and release capture within the shutdown budget."""
        self.stop_event.set()
        stream = self.stream
        if stream is not None:
            stream.abort()
        if self.thread:
            self.thread.join(timeout=0.4)
