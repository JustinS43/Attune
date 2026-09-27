"""Local EfficientAT waveform scorer with explicit AudioSet class names."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

# EfficientAT was trained on 10 s AudioSet clips; on 1-3 s inputs its logits blow up
# (every class near 1.0). Shorter context is repeated out to this length.
CLIP_SAMPLES = 10 * 32000


class SoundModel:
    """Load an exported waveform-to-logits TorchScript model and its label order."""

    def __init__(self, config: dict):
        import torch

        for key in ("model_path", "labels_path"):
            if not Path(config[key]).is_file():
                raise FileNotFoundError(config[key])
        self.labels = json.loads(Path(config["labels_path"]).read_text())
        required = {"Fire alarm", "Doorbell", "Ding-dong", "Knock", "Speech", "Music"}
        if not required.issubset(self.labels) or not (
            {"Smoke detector", "Smoke detector, smoke alarm"} & set(self.labels)
        ):
            raise ValueError("EfficientAT label manifest lacks required named classes")
        if len(self.labels) != len(set(self.labels)):
            raise ValueError("duplicate class names")
        self.device = config["device"]
        self.model = torch.jit.load(config["model_path"], map_location=self.device).eval()

    def score(self, samples: np.ndarray) -> dict[str, float]:
        """Score the latest 1-10 s of 32 kHz mono PCM as one 10 s clip."""
        import torch

        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1 or len(samples) < 32000:
            raise ValueError("expected at least one second at 32 kHz")
        samples = samples[-CLIP_SAMPLES:]
        if len(samples) < CLIP_SAMPLES:
            samples = np.resize(samples, CLIP_SAMPLES)  # repeats the audio
        with torch.inference_mode():
            logits = self.model(torch.from_numpy(samples[None, :]).to(self.device))
            scores = torch.sigmoid(logits).flatten().cpu().tolist()
        if len(scores) != len(self.labels):
            raise ValueError("model and class manifest disagree")
        return dict(zip(self.labels, scores))
