"""Silero's 512-sample inference and hysteretic utterance segmentation."""

from __future__ import annotations

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
