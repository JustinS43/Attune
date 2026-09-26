# Vendored from Light-ASD, loss.py (the lossAV head only, inference branch)
# https://github.com/Junhua-Liao/Light-ASD (commit ed38c232de5efe0261dbd68627c0ade7cdfe14eb)
# Copyright (c) 2023 Liao Junhua. MIT License, see LICENSE in this folder.
# Changes: only the `labels is None` branch of lossAV.forward is kept (no training), and it
# returns the tensor instead of a numpy copy; lossV (a training-only head) is dropped.
"""Light-ASD's speaking head: 128-d frame features -> 2 logits; the score is logit[1]."""

from torch import nn


class lossAV(nn.Module):
    def __init__(self):
        super().__init__()
        self.FC = nn.Linear(128, 2)

    def forward(self, x):
        """Per-frame speaking score: the raw class-1 logit (upstream thresholds it at 0)."""
        x = x.squeeze(1)
        x = self.FC(x)
        return x[:, 1]
