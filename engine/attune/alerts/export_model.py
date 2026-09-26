"""Export preinstalled EfficientAT code/weights; never fetch code or weights.

Run from an installed upstream EfficientAT checkout on PYTHONPATH:
uv run --project engine python -m attune.alerts.export_model --weights PATH --output PATH --labels PATH
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def export(weights: Path, output: Path, labels_path: Path) -> None:
    """Bundle the upstream mel frontend with mn10_as and its matching class order."""
    import torch
    from helpers.utils import labels
    from models.mn.model import get_model
    from models.preprocess import AugmentMelSTFT

    class WaveformModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.mel = AugmentMelSTFT(n_mels=128, sr=32000, win_length=800, hopsize=320)
            self.net = get_model(width_mult=1.0, pretrained_name=None)
            self.net.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))

        def forward(self, waveform):
            logits, _ = self.net(self.mel(waveform).unsqueeze(1))
            return logits

    model = WaveformModel().eval()
    traced = torch.jit.trace(model, torch.zeros(1, 32000))
    output.parent.mkdir(parents=True, exist_ok=True)
    traced.save(str(output))
    labels_path.write_text(json.dumps(list(labels)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args()
    export(args.weights, args.output, args.labels)
