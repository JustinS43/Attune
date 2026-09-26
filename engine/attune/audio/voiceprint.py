"""CAM++ embeddings; consented disk prints and forgettable session prints."""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class CAMExtractor:
    """Extract a normalized speaker vector from local CAM++ weights."""

    def __init__(self, path: str, provider: str = "cpu"):
        import sherpa_onnx

        if not Path(path).is_file():
            raise FileNotFoundError(path)
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=path, provider=provider, num_threads=1
        )
        if not cfg.validate():
            raise ValueError("invalid CAM++ configuration")
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=samples)
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            raise ValueError("not enough voice audio")
        return np.asarray(self.extractor.compute(stream), dtype=np.float32)


class VoicePrints:
    """Persistent prints require explicit consent; harvested updates stay in RAM."""

    def __init__(
        self, root: Path, extract: Callable, threshold: float, enroll_s: float, match_s: float
    ):
        self.root, self.extract, self.threshold = root, extract, threshold
        self.enroll_s, self.match_s = enroll_s, match_s
        self.enrolled: dict[str, np.ndarray] = {}
        self.session: dict[str, np.ndarray] = {}
        if root.exists():
            for path in root.glob("*/voice.json"):
                try:
                    self._path(path.parent.name)
                    data = json.loads(path.read_text())
                    if data.get("consent") is True and self._consented_at(data.get("consent_t")):
                        self.enrolled[path.parent.name] = self._unit(data["embedding"])
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    logger.warning("Ignoring an invalid local voice enrollment")

    @staticmethod
    def _consented_at(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    @staticmethod
    def _unit(vector) -> np.ndarray:
        v = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(v))
        if v.ndim != 1 or not np.all(np.isfinite(v)) or norm == 0:
            raise ValueError("invalid voice embedding")
        return v / norm

    def _path(self, person_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", person_id):
            raise ValueError("invalid person identifier")
        path = self.root / person_id / "voice.json"
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("voice path escapes people directory")
        return path

    def enroll(
        self,
        person_id: str,
        samples: np.ndarray,
        consent: bool,
        consent_t: float,
        *,
        guard: Callable | None = None,
        lock=None,
    ) -> None:
        """Save only the embedding from sufficient consented speech."""
        if (
            consent is not True
            or not self._consented_at(consent_t)
            or len(samples) < self.enroll_s * 16000
        ):
            raise ValueError("consent and sufficient speech are required")
        path = self._path(person_id)
        vector = self._unit(self.extract(samples))
        with lock if lock is not None else nullcontext():
            if guard is not None and not guard():
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"consent": True, "consent_t": consent_t, "embedding": vector.tolist()})
            )
            temporary.replace(path)
            self.enrolled[person_id] = vector

    def harvest(self, person_id: str, samples: np.ndarray) -> None:
        """Extend a session-only print, including for an enrolled person."""
        if len(samples) < self.match_s * 16000:
            return
        vector = self._unit(self.extract(samples))
        prior = self.session.get(person_id)
        self.session[person_id] = self._unit(vector + prior) if prior is not None else vector

    def match(self, samples: np.ndarray) -> tuple[str | None, float]:
        """Return no identity below the configured similarity or duration gate."""
        if len(samples) < self.match_s * 16000 or not (self.enrolled or self.session):
            return None, 0.0
        vector = self._unit(self.extract(samples))
        scores = [
            (float(np.dot(vector, value)), key)
            for key, value in (self.enrolled | self.session).items()
        ]
        score, person = max(scores)
        return (person if score >= self.threshold else None), score

    def delete(self, person_id: str) -> None:
        """Remove this section's persistent and session data for one person."""
        path = self._path(person_id)
        self.enrolled.pop(person_id, None)
        self.session.pop(person_id, None)
        path.unlink(missing_ok=True)

    def forget(self) -> None:
        """Retain consented enrollment; wipe all harvested session vectors."""
        self.session.clear()
