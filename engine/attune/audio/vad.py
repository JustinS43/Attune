"""Silero's 512-sample inference and hysteretic utterance segmentation."""

from __future__ import annotations

from collections import deque

import numpy as np


class SileroVAD:
    """Load the installed Silero package's bundled model without downloading."""

    def __init__(self):
        from silero_vad import load_silero_vad

        self.model = load_silero_vad(onnx=False)

    def __call__(self, samples: np.ndarray) -> float:
        import torch

        with torch.inference_mode():
            return float(self.model(torch.from_numpy(samples), 16000).item())

    def reset(self) -> None:
        self.model.reset_states()


class InputGain:
    """Slow automatic gain in front of the VAD, so quiet or distant speech is detected.

    Silero hears speech at -46 dBFS but misses most of it at -66 (0-23% of frames). This
    brings the loudest recent frames (the 90th percentile of 32 ms frame levels over
    `window_s`) to `target_dbfs`, with 0 to `max_db` of gain, falling fast and rising
    slowly, and never so much that the quiet frames (10th percentile over
    `floor_window_s`: the room's floor) go above `floor_dbfs`. Only the VAD hears it; the
    recogniser has its own level matching.
    """

    def __init__(self, config: dict):
        self.target = config.get("vad_gain_target_dbfs", -26.0)
        self.max_db = config.get("vad_gain_max_db", 30.0)
        self.floor_db = config.get("vad_gain_floor_dbfs", -45.0)
        self.recent: deque[float] = deque(
            maxlen=round(config.get("vad_gain_window_s", 1.5) / 0.032)
        )
        self.floor: deque[float] = deque(
            maxlen=round(config.get("vad_gain_floor_window_s", 10.0) / 0.032)
        )
        self.gain_db = 0.0

    def __call__(self, frame: np.ndarray) -> np.ndarray:
        level = 20 * np.log10(float(np.sqrt(np.mean(frame * frame))) + 1e-9)
        self.recent.append(level)
        self.floor.append(level)
        want = self.target - float(np.percentile(self.recent, 90))
        want = min(want, self.floor_db - float(np.percentile(self.floor, 10)))
        want = min(max(want, 0.0), self.max_db)
        # fast down (a loud talker must not clip), slow up (a pause is not a cue to boost)
        self.gain_db += (want - self.gain_db) * (0.5 if want < self.gain_db else 0.1)
        if self.gain_db < 0.05:
            return frame
        return np.clip(frame * 10 ** (self.gain_db / 20), -1.0, 1.0).astype(np.float32)


class Segmenter:
    """Apply start/end hysteresis, minimum speech and trailing-silence rules."""

    def __init__(self, config: dict):
        self.config = config
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.start = None
        self.speech_s = 0.0
        self.silence_s = 0.0
        self.confirmed = False
        self.resumed = False

    def resume(self, silence_s: float) -> None:
        """Carry on after a split: the next frame starts a confirmed utterance at once, and
        the pause so far still counts towards its end (A-22). The hysteresis state stays:
        a split at max_utterance_s can come in the middle of a word."""
        active = self.active
        self.reset()
        self.active = active
        self.speech_s = self.config["min_speech_ms"] / 1000
        self.silence_s = silence_s
        self.confirmed = True
        self.resumed = True

    def feed(self, t: float, prob: float, duration: float = 0.032) -> tuple[bool, bool, bool]:
        """Return speech state, first confirmed frame, and utterance ending."""
        if not self.active and prob > self.config["vad_start"]:
            self.active = True
        elif self.active and prob < self.config["vad_end"]:
            self.active = False
        if self.resumed:
            self.start, self.resumed = t, False
        if self.active:
            if self.start is None:
                self.start = t
            self.speech_s += duration
            self.silence_s = 0
        elif self.start is not None:
            self.silence_s += duration
        began = not self.confirmed and self.speech_s >= self.config["min_speech_ms"] / 1000
        self.confirmed |= began
        ended = self.start is not None and self.silence_s >= self.config["end_silence_ms"] / 1000
        return self.active, began, ended
