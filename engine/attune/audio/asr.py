"""Local sherpa-onnx Nemotron 3.5 streaming transducer adapter."""

from __future__ import annotations

import re
from collections import deque
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
    """Gain for the recogniser, which is level-sensitive (WER 0.02 from -12 to -36 dBFS,
    0.05 at -46, 0.34 at -56).

    By default the first chunk's RMS sets the gain, which is then only lowered (never
    clips), so trailing noise is never raised. With `rise_db_s`, the gain follows the
    utterance instead: a `percentile` of the 32 ms frame levels over `window_s` is brought
    to `target`, the gain falls at once and rises by at most `rise_db_s` a second. A gain
    fixed by a loud first chunk (background talk, a laugh) left a quieter talker after it
    under-amplified: WER 0.27 and 0.41 against 0.08 and 0.10 (median over 0.5 s, 60 dB/s).
    """

    def __init__(
        self,
        target: float,
        window_s: float = 0.5,
        rise_db_s: float = 0.0,
        max_db: float = 40.0,
        percentile: float = 50.0,
    ) -> None:
        self.target = target
        self.rise_db_s = rise_db_s
        self.percentile = percentile
        self.max_gain = 10 ** (max_db / 20)
        self.levels: deque[float] = deque(maxlen=max(1, round(window_s / 0.032)))
        self.reset()

    def reset(self) -> None:
        """Begin a new utterance with no inherited gain or PCM."""
        self.gain: float | None = None
        self.levels.clear()

    def feed(self, samples: np.ndarray) -> np.ndarray:
        """Apply the gain, reducing it at once to avoid clipping."""
        if not len(samples):
            return samples.copy()
        peak = float(np.max(np.abs(samples)))
        if self.gain is None and not self.rise_db_s:
            rms = float(np.sqrt(np.mean(samples * samples)))
            self.gain = self.target / rms if rms > np.finfo(np.float32).eps else 1.0
        elif self.rise_db_s:
            for i in range(0, len(samples), 512):
                block = samples[i : i + 512]
                self.levels.append(float(np.sqrt(np.mean(block * block))))
            level = float(np.percentile(self.levels, self.percentile))
            want = self.target / level if level > np.finfo(np.float32).eps else 1.0
            want = min(want, self.max_gain)
            if self.gain is None or want < self.gain:
                self.gain = want
            else:
                step = 10 ** (self.rise_db_s * len(samples) / 16000 / 20)
                self.gain = min(want, self.gain * step)
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


def hold_back(
    tokens: list, base: int, times: list | None = None, heard_s: float = 0.0, settle_s: float = 1.5
) -> int:
    """Where to cut a stream's tokens (after `base`) so no word is split: before the
    last word, unless it ends with punctuation, it is the only word, or the stream has
    heard `settle_s` of audio after it (its next piece would have been decoded by then)."""
    starts = []
    for i in range(base, len(tokens)):
        if tokens[i].startswith("<"):
            continue
        if tokens[i].startswith(("▁", " ")) or not starts:
            starts.append(i)

    def spelled(a: int, b: int) -> str:
        return "".join(t for t in tokens[a:b] if not t.startswith("<")).replace("▁", "").strip()

    keep = len(tokens)
    while starts and not spelled(starts[-1], keep):
        keep = starts.pop()  # a lone word mark goes with the word after it
    settled = times is not None and keep and float(times[keep - 1]) < heard_s - settle_s
    last = spelled(starts[-1], keep) if starts else ""
    if len(starts) > 1 and not settled and not last.endswith((".", ",", "?", "!", ";", ":")):
        keep = starts[-1]
    elif not starts:
        keep = len(tokens)
    return keep


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
        self.stream = self._new_stream()
        self.samples = 0
        self.base = 0  # tokens before this belong to utterances already cut off (see cut)

    def _new_stream(self) -> Any:
        stream = self.recognizer.create_stream()
        languages = self.config.get("languages", ["en"])
        self.language = languages[0] if len(languages) == 1 else "und"
        if hasattr(stream, "set_option"):
            stream.set_option("language", self.language if self.language != "und" else "auto")
        elif languages != ["en"]:
            raise RuntimeError("installed sherpa-onnx lacks per-stream language support")
        return stream

    def feed(self, samples: np.ndarray, final: bool = False) -> Recognition:
        """Return the latest hypothesis and model-derived word times.

        The model decodes in fixed chunks (560 ms for the exported Nemotron: a 650 ms window
        moved by 560 ms). Feed it small steps (`[audio] asr_chunk_ms`) so each chunk is
        decoded as soon as its audio is in, instead of waiting for the next big step.
        """
        self.samples += len(samples)
        self.stream.accept_waveform(16000, samples)
        if final:
            self.stream.accept_waveform(
                16000, np.zeros(round(self.config["flush_s"] * 16000), np.float32)
            )
            self.stream.input_finished()
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        return self._parse(
            self.recognizer.get_result_all(self.stream), self.samples / 16000, self.base
        )

    def cut(self) -> Recognition:
        """End the current utterance at what is decoded so far, and carry on listening.

        The stream and its context go on (no flush): the next utterance's words are the
        tokens decoded after this point, so a split in the middle of talk loses no words
        (a fresh stream drops the first words of quiet speech). A word not decoded yet
        goes to the next utterance. The last word decoded goes there too, unless it ends
        with punctuation: the model decodes in 560 ms chunks, and a chunk can end inside
        a word ("North Ca" | "rolina"). Word times stay relative to the stream's start.
        """
        result = self.recognizer.get_result_all(self.stream)
        if isinstance(result, str):
            import json

            result = json.loads(result)
        if isinstance(result, dict):
            tokens, times = list(result.get("tokens", [])), list(result.get("timestamps", []))
        else:
            tokens, times = list(result.tokens), list(result.timestamps)
        heard = self.samples / 16000
        keep = hold_back(tokens, self.base, times, heard, self.config.get("word_settle_s", 1.5))
        end = float(times[keep]) if keep < len(times) else self.samples / 16000
        segment = self._parse(result, min(end, self.samples / 16000), self.base, keep)
        self.base = keep
        return segment

    def rescue(self, samples: np.ndarray, lock: Any, gap_s: float = 0.2) -> Recognition:
        """Decode a short utterance the streaming pass heard as nothing, on its own stream.

        Very short replies ("No", "Hi") on a fresh stream often come out empty: the model
        has no context yet. Heard twice in a row, they are recognised, so the utterance is
        decoded as [audio, gap, audio]. The model emits in 560 ms steps, so its word times
        cannot say which copy a word came from: when the words repeat ("Yes, yes") one copy
        is kept, else all of them. Word times are relative to the start of `samples`.
        The recogniser is shared with the live stream, so every call into it holds `lock`,
        one chunk at a time.
        """
        gap = np.zeros(round(gap_s * 16000), np.float32)
        audio = np.concatenate([samples, gap, samples]).astype(np.float32)
        second = (len(samples) + len(gap)) / 16000
        with lock:
            stream = self._new_stream()
        step = 8960
        for i in range(0, len(audio), step):
            with lock:
                stream.accept_waveform(16000, audio[i : i + step])
                while self.recognizer.is_ready(stream):
                    self.recognizer.decode_stream(stream)
        with lock:
            stream.accept_waveform(
                16000, np.zeros(round(self.config["flush_s"] * 16000), np.float32)
            )
            stream.input_finished()
            while self.recognizer.is_ready(stream):
                self.recognizer.decode_stream(stream)
            both = self._parse(self.recognizer.get_result_all(stream), len(audio) / 16000)
        words = both.words
        plain = [re.sub(r"[^\w']", "", w).lower() for w, _, _ in words]
        half = len(words) // 2
        if half and len(words) % 2 == 0 and plain[:half] == plain[half:]:
            words = words[:half] if words[0][1] < second - 0.05 else words[half:]
            last = words[-1][0]
            words[-1] = (last.rstrip(",;:"), words[-1][1], words[-1][2])
        duration = len(samples) / 16000

        def local(t: float) -> float:
            return min(duration, max(0.0, t - second if t >= second - 0.05 else t))

        words = [(w, local(a), max(local(a), local(b))) for w, a, b in words]
        text = " ".join(w for w, _, _ in words).strip()
        return Recognition(text[:1].upper() + text[1:], both.lang, words)

    def _parse(
        self, result: Any, duration: float, base: int = 0, end: int | None = None
    ) -> Recognition:
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
        if base or end is not None:
            # only the tokens from `base` (the last cut) to `end`, and the text they spell
            tokens, times = list(tokens)[base:end], list(times)[base:end]
            text = "".join(t for t in tokens if not t.startswith("<")).replace("▁", " ")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()
        return Recognition(
            text, language.split("-")[0].lower(), token_words(tokens, times, duration)
        )
