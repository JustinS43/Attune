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

    def __init__(self, seconds: float = 30, rate: int = 16000):
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


class MicReader:
    """Read 48 kHz blocks; reconnect to the default input after device loss."""

    def __init__(self, config: dict, clock: Callable, deliver: Callable, sd: Any = None):
        self.config, self.clock, self.deliver, self.sd = config, clock, deliver, sd
        self.stop_event = threading.Event()
        self.thread = None
        self.stream = None
        self.queue: queue.Queue = queue.Queue(maxsize=200)
        self.dropped = 0
        self.detail = "not started"

    def start(self) -> None:
        """Load lightweight device bindings and launch capture."""
        if self.sd is None:
            import sounddevice

            self.sd = sounddevice
        import soxr

        rate = self.config["sample_rate"]
        self.resamplers = {
            r: soxr.ResampleStream(rate, r, 1, dtype="float32") for r in (16000, 32000)
        }
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="mic")
        self.thread.start()

    def _device(self, fallback: bool) -> int | None:
        if fallback:
            return None
        name = self.config["device_name"].casefold()
        devices, hosts = self.sd.query_devices(), self.sd.query_hostapis()
        for i, device in enumerate(devices):
            if (
                device["max_input_channels"]
                and name in device["name"].casefold()
                and (sys.platform != "win32" or "WASAPI" in hosts[device["hostapi"]]["name"])
            ):
                return i
        return None

    def _callback(self, data: np.ndarray, frames: int, timing: Any, status: Any) -> None:
        if status:
            self.detail = "capture overflow"
        # PortAudio clocks are relative to its stream; translate through currentTime.
        t = self.clock() - max(0.0, timing.currentTime - timing.inputBufferAdcTime)
        try:
            self.queue.put_nowait((t, data[:, 0].copy(), bool(status)))
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        fallback = False
        while not self.stop_event.is_set():
            try:
                device = self._device(fallback)
                for resampler in self.resamplers.values():
                    resampler.clear()
                origin, produced = None, {16000: 0, 32000: 0}
                previous_end = None
                with self.sd.InputStream(
                    device=device,
                    samplerate=self.config["sample_rate"],
                    blocksize=round(self.config["sample_rate"] * self.config["block_ms"] / 1000),
                    channels=1,
                    dtype="float32",
                    callback=self._callback,
                ) as stream:
                    self.stream = stream
                    self.detail = "default mic" if device is None else "preferred mic"
                    while not self.stop_event.is_set():
                        try:
                            t, samples, overflow = self.queue.get(timeout=0.2)
                        except queue.Empty:
                            if not stream.active:
                                raise RuntimeError("microphone disconnected")
                            continue
                        if overflow or (previous_end is not None and abs(t - previous_end) > 0.005):
                            for resampler in self.resamplers.values():
                                resampler.clear()
                            origin, produced = None, {16000: 0, 32000: 0}
                        previous_end = t + len(samples) / self.config["sample_rate"]
                        if origin is None:
                            origin = t
                        # Resampler output counts, rather than callback arrival jitter, timestamp PCM.
                        for rate, resampler in self.resamplers.items():
                            out = resampler.resample_chunk(samples)
                            if len(out):
                                self.deliver(
                                    {
                                        "t": origin + produced[rate] / rate,
                                        "sample_rate": rate,
                                        "samples": out,
                                    }
                                )
                                produced[rate] += len(out)
            except Exception:
                logger.exception("microphone unavailable; retrying default input")
                self.detail, fallback = "microphone unavailable", True
                self.stop_event.wait(0.5)
            finally:
                self.stream = None
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        break

    def stop(self) -> None:
        """Release capture within the service shutdown budget."""
        self.stop_event.set()
        if self.stream:
            self.stream.abort()
        if self.thread:
            self.thread.join(timeout=0.4)
