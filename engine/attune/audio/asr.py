"""Local sherpa-onnx Nemotron 3.5 streaming transducer adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class Recognition:
    text: str = ""
    lang: str = "en"
    words: list[tuple[str, float, float]] = field(default_factory=list)


def level_match(samples: np.ndarray, target: float) -> np.ndarray:
    """Normalize an utterance without a noise filter or clipping."""
    rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0
    if rms <= np.finfo(np.float32).eps:
        return samples
    gain = min(target / rms, 0.99 / max(float(np.max(np.abs(samples))), 1e-8))
    return np.asarray(samples * gain, dtype=np.float32)


class UtteranceLevel:
    """Choose gain from the first speech chunk; never amplify trailing noise anew."""

    def __init__(self, target: float) -> None:
        self.target = target
        self.reset()

    def reset(self) -> None:
        """Begin a new utterance with no inherited gain or PCM."""
        self.gain: float | None = None

    def feed(self, samples: np.ndarray) -> np.ndarray:
        """Apply consistent gain, reducing it only to avoid clipping."""
        if not len(samples):
            return samples.copy()
        peak = float(np.max(np.abs(samples)))
        if self.gain is None:
            rms = float(np.sqrt(np.mean(samples * samples)))
            self.gain = self.target / rms if rms > np.finfo(np.float32).eps else 1.0
        if peak:
            self.gain = min(self.gain, 0.99 / peak)
        return np.asarray(samples * self.gain, dtype=np.float32)


def token_words(tokens: list, times: list, duration: float) -> list[tuple[str, float, float]]:
    """Combine sentencepiece tokens; clamp model timestamps to real PCM duration."""
    result: list[tuple[str, float, float]] = []
    if len(tokens) != len(times):
        return result
    for i, (token, t) in enumerate(zip(tokens, times)):
        if token.startswith("<"):
            continue
        start = min(duration, max(0.0, float(t)))
        end = min(duration, max(start, float(times[i + 1]) if i + 1 < len(times) else duration))
        if token.startswith(("▁", " ")) or not result:
            result.append((token.lstrip("▁ "), start, end))
        else:
            word, a, _ = result[-1]
            result[-1] = (word + token, a, end)
    return [item for item in result if item[0]]


class NemotronASR:
    """Decode one streaming utterance at a time from explicitly local weights."""

    def __init__(self, config: dict, recognizer: Any = None):
        self.config = config
        if recognizer is None:
            import sherpa_onnx

            paths = {name: config[name] for name in ("tokens", "encoder", "decoder", "joiner")}
            for path in paths.values():
                if not Path(path).is_file():
                    raise FileNotFoundError(path)
            recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                **paths,
                sample_rate=16000,
                feature_dim=80,
                provider=config["provider"],
                num_threads=config["num_threads"],
                enable_endpoint_detection=False,
            )
        self.recognizer = recognizer
        self.reset()

    def reset(self) -> None:
        self.stream = self.recognizer.create_stream()
        languages = self.config.get("languages", ["en"])
        self.language = languages[0] if len(languages) == 1 else "und"
        if hasattr(self.stream, "set_option"):
            self.stream.set_option("language", self.language if self.language != "und" else "auto")
        elif languages != ["en"]:
            raise RuntimeError("installed sherpa-onnx lacks per-stream language support")
        self.samples = 0

    def feed(self, samples: np.ndarray, final: bool = False) -> Recognition:
        """Return the latest hypothesis and model-derived word times."""
        self.samples += len(samples)
        self.stream.accept_waveform(16000, samples)
        if final:
            self.stream.accept_waveform(
                16000, np.zeros(round(self.config["flush_s"] * 16000), np.float32)
            )
            self.stream.input_finished()
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        result = self.recognizer.get_result_all(self.stream)
        if isinstance(result, str):
            import json

            result = json.loads(result)
        if isinstance(result, dict):
            text, tokens, times = (
                result.get("text", ""),
                result.get("tokens", []),
                result.get("timestamps", []),
            )
            language = result.get("lang") or result.get("language") or self.language
        else:
            text, tokens, times = result.text, result.tokens, result.timestamps
            language = getattr(result, "lang", None) or getattr(result, "language", None)
            language = language or self.language
        # Current sherpa removes Nemotron's auto-language tags; accept them as well
        # for compatible older adapters without showing special tokens in captions.
        tag = re.search(r"<([a-z]{2,3})(?:-[A-Za-z]{2})?>", text)
        if tag:
            language = tag[1]
        text = re.sub(r"<[^>]+>", "", text).strip()
        return Recognition(
            text, language.split("-")[0].lower(), token_words(tokens, times, self.samples / 16000)
        )
