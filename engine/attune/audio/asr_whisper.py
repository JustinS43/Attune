"""Whisper local-agreement drafts and multilingual finals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .asr import Recognition


class WhisperASR:
    """Re-decode a bounded utterance, exposing only consecutive-pass agreement."""

    def __init__(self, config: dict, model: Any = None):
        self.config = config
        if model is None:
            from faster_whisper import WhisperModel

            if not Path(config["model_path"]).is_dir():
                raise FileNotFoundError(config["model_path"])
            model = WhisperModel(
                config["model_path"],
                device=config["device"],
                compute_type=config["compute_type"],
                local_files_only=True,
            )
        self.model = model
        self.reset()

    def reset(self) -> None:
        self.audio: list[np.ndarray] = []
        self.previous: list = []

    def feed(self, samples: np.ndarray, final: bool = False) -> Recognition:
        """Return stable words on drafts and the complete hypothesis on finals."""
        self.audio.append(samples.copy())
        audio = np.concatenate(self.audio)
        langs = self.config["languages"]
        segments, info = self.model.transcribe(
            audio,
            word_timestamps=True,
            language=langs[0] if len(langs) == 1 else None,
            beam_size=1,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        segments = list(segments)
        duration = len(audio) / 16000
        words = [
            (
                w.word.strip(),
                min(duration, max(0.0, w.start)),
                min(duration, max(0.0, w.start, w.end)),
            )
            for segment in segments
            for w in (segment.words or [])
            if w.word.strip()
        ]
        stable = words
        if not final:
            n = 0
            for a, b in zip(words, self.previous):
                if a[0] != b[0]:
                    break
                n += 1
            stable = words[:n]
        self.previous = words
        text = " ".join(w[0] for w in stable)
        if final and all(hasattr(segment, "text") for segment in segments):
            # Segment text preserves punctuation and scripts without word spaces.
            text = "".join(segment.text for segment in segments).strip()
        return Recognition(text, info.language, stable)
