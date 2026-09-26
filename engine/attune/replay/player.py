"""Feeds a WAV file onto the bus as `audio.block`, like the microphone.

Section 4 - Pages, Engine & Demo. TODO: P-13 (ported from vision/devview.py).

Laptop mic arrays cancel their own speakers' output, so a clip played aloud never
reaches the mic; this is how captions are tried with a recording. Blocks go out in
real time on the shared clock at 16 kHz (speech) and 32 kHz (alerts), 10 ms each,
exactly as `AudioService`'s capture would publish them (contracts section 2).
"""

from __future__ import annotations

import logging
import threading
import wave
from collections.abc import Callable

import numpy as np

from ..core.contracts import AUDIO_BLOCK

log = logging.getLogger(__name__)

RATES = (16000, 32000)
BLOCK = 160  # samples at 16 kHz = 10 ms


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Mono float32 samples in [-1, 1] and the file's sample rate (16-bit PCM only)."""
    with wave.open(path, "rb") as w:
        width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("expected a 16-bit WAV file")
    pcm = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    return pcm.reshape(-1, channels).mean(axis=1), rate


def resample(pcm: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target:
        return pcm.astype(np.float32)
    try:
        import soxr

        return soxr.resample(pcm, rate, target).astype(np.float32)
    except ImportError:  # linear fallback, good enough for tests
        n = round(len(pcm) * target / rate)
        x = np.linspace(0, len(pcm) - 1, n)
        return np.interp(x, np.arange(len(pcm)), pcm).astype(np.float32)


class FilePlayer:
    """Publish a WAV file as real-time `audio.block` events, optionally repeating."""

    def __init__(
        self,
        bus,
        path: str,
        clock: Callable[[], float],
        delay_s: float = 3.0,
        repeat_s: float = 0.0,
    ) -> None:
        pcm, rate = read_wav(path)
        self.streams = {r: resample(pcm, rate, r) for r in RATES}
        self.path = path
        self.bus, self.clock, self.delay_s, self.repeat_s = bus, clock, delay_s, repeat_s
        self.duration_s = len(self.streams[16000]) / 16000
        self.plays = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="file-audio", daemon=True)

    def start(self) -> None:
        log.info(
            "Playing %s (%.1f s) into the pipeline in %.0f s%s",
            self.path,
            self.duration_s,
            self.delay_s,
            f", repeating every {self.repeat_s:.0f} s" if self.repeat_s else "",
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def _run(self) -> None:
        if self.stop_event.wait(self.delay_s):
            return
        while not self.stop_event.is_set():
            t0 = self.clock()
            n16 = len(self.streams[16000])
            for i in range(0, n16, BLOCK):
                t = t0 + i / 16000
                if self.stop_event.wait(max(0.0, t - self.clock())):
                    return
                for rate, data in self.streams.items():
                    a, b = i * rate // 16000, min(len(data), (i + BLOCK) * rate // 16000)
                    if b > a:
                        self.bus.publish(
                            AUDIO_BLOCK, {"t": t, "sample_rate": rate, "samples": data[a:b]}
                        )
            self.plays += 1
            log.info("Finished playing %s into the pipeline", self.path)
            if not self.repeat_s or self.stop_event.wait(self.repeat_s):
                return
