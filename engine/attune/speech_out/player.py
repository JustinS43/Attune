"""Plays audio chunks on the laptop speakers as they arrive (TODO H-09).

``play`` calls ``on_start`` right before the first sample is written, so the
``speech_out.playing`` start event lines up with sound actually leaving the speakers.
If the default device refuses the stream's rate, audio is resampled to the device rate.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

import numpy as np

logger = logging.getLogger(__name__)

BLOCK = 2048  # samples per write, so a cancel is honoured within ~0.1 s


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linear resampling; good enough for speech."""
    if src == dst or not len(x):
        return x
    n = max(1, round(len(x) * dst / src))
    positions = np.linspace(0, len(x) - 1, n)
    return np.interp(positions, np.arange(len(x)), x).astype(np.float32)


class SoundDevicePlayer:
    """Default output device through sounddevice (PortAudio)."""

    def __init__(self, device=None, volume: float = 1.0):
        self.device = device
        self.volume = float(volume)
        import sounddevice  # noqa: F401 - initialise PortAudio now, not on the first reply

    def _open(self, sample_rate: int):
        import sounddevice as sd

        try:
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                device=self.device,
                latency="low",
            )
            return stream, sample_rate
        except sd.PortAudioError:
            info = sd.query_devices(self.device, kind="output")
            rate = int(info["default_samplerate"])
            logger.info("speech_out: device refuses %d Hz; resampling to %d", sample_rate, rate)
            stream = sd.OutputStream(
                samplerate=rate, channels=1, dtype="float32", device=self.device, latency="low"
            )
            return stream, rate

    def play(
        self,
        chunks: Iterable[np.ndarray],
        sample_rate: int,
        cancel: threading.Event,
        on_start: Callable[[], None] | None = None,
    ) -> float:
        """Blocking: play every chunk, return seconds played. Honours ``cancel``."""
        stream, rate = self._open(sample_rate)
        played = 0
        started = False
        try:
            stream.start()
            for chunk in chunks:
                if cancel.is_set():
                    break
                data = resample(np.asarray(chunk, dtype=np.float32), sample_rate, rate)
                if self.volume != 1.0:
                    data = np.clip(data * self.volume, -1.0, 1.0)
                for i in range(0, len(data), BLOCK):
                    if cancel.is_set():
                        break
                    if not started:
                        started = True
                        if on_start:
                            on_start()
                    block = data[i : i + BLOCK]
                    stream.write(block.reshape(-1, 1))
                    played += len(block)
        finally:
            try:
                if cancel.is_set():
                    stream.abort()
                else:
                    stream.stop()  # waits until queued audio has played
            finally:
                stream.close()
        return played / rate
