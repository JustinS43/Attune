# Vendored from Light-ASD, model/Model.py
# https://github.com/Junhua-Liao/Light-ASD (commit ed38c232de5efe0261dbd68627c0ade7cdfe14eb)
# Copyright (c) 2023 Liao Junhua. MIT License, see LICENSE in this folder.
# Changes: re-indented to 4 spaces; relative imports, `from torch import nn`; logic unchanged.
"""Light-ASD audio-visual model (about 1 M parameters)."""

import torch
from torch import nn

from .classifier import BGRU
from .encoder import audio_encoder, visual_encoder


class ASD_Model(nn.Module):
    def __init__(self):
        super().__init__()

        self.visualEncoder = visual_encoder()
        self.audioEncoder = audio_encoder()
        self.GRU = BGRU(128)

    def forward_visual_frontend(self, x):
        B, T, W, H = x.shape
        x = x.view(B, 1, T, W, H)
        x = (x / 255 - 0.4161) / 0.1688
        x = self.visualEncoder(x)
        return x

    def forward_audio_frontend(self, x):
        x = x.unsqueeze(1).transpose(2, 3)
        x = self.audioEncoder(x)
        return x

    def forward_audio_visual_backend(self, x1, x2):
        x = x1 + x2
        x = self.GRU(x)
        x = torch.reshape(x, (-1, 128))
        return x

    def forward_visual_backend(self, x):
        x = torch.reshape(x, (-1, 128))
        return x

    def forward(self, audioFeature, visualFeature):
        audioEmbed = self.forward_audio_frontend(audioFeature)
        visualEmbed = self.forward_visual_frontend(visualFeature)
        outsAV = self.forward_audio_visual_backend(audioEmbed, visualEmbed)
        outsV = self.forward_visual_backend(visualEmbed)

        return outsAV, outsV
