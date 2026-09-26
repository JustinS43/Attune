"""Local EfficientAT waveform scorer with explicit AudioSet class names."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class SoundModel:
    """Load an exported waveform-to-logits TorchScript model and its label order."""

    def __init__(self, config: dict):
        import torch

        for key in ("model_path", "labels_path"):
            if not Path(config[key]).is_file():
                raise FileNotFoundError(config[key])
        self.labels = json.loads(Path(config["labels_path"]).read_text())
        required = {"Fire alarm", "Doorbell", "Ding-dong", "Speech", "Music"}
        if not required.issubset(self.labels) or not (
            {"Smoke detector", "Smoke detector, smoke alarm"} & set(self.labels)
        ):
            raise ValueError("EfficientAT label manifest lacks required named classes")
        if len(self.labels) != len(set(self.labels)):
            raise ValueError("duplicate class names")
        self.device = config["device"]
        self.model = torch.jit.load(config["model_path"], map_location=self.device).eval()

    def score(self, samples: np.ndarray) -> dict[str, float]:
        """Score one second of 32 kHz mono PCM; label positions come from export."""
        import torch

        if len(samples) != 32000:
            raise ValueError("expected one second at 32 kHz")
        with torch.inference_mode():
            logits = self.model(torch.from_numpy(samples[None, :]).to(self.device))
            scores = torch.sigmoid(logits).flatten().cpu().tolist()
        if len(scores) != len(self.labels):
            raise ValueError("model and class manifest disagree")
        return dict(zip(self.labels, scores))
