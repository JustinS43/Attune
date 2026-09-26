"""CAM++ embeddings; consented disk prints and forgettable session prints.

A saved person's `data/people/<id>/voice.json` holds their consent, one base print
(`embedding`) and where it was made (`source`: "station" = the laptop mic at the enrollment
station, V-23 / A-21; "glasses" = the glasses mic, the original P-29 flow; files without
it are glasses prints). Only prints are stored, never audio.

Cross-mic (A-21). A station print is made on the laptop mic but heard on the glasses mic.
`station_match` is its own threshold: its scores are shifted by `threshold - station_match`,
so everything downstream compares every score with the one `threshold` ([fusion]
voice_match). The simulated laptop-to-glasses study (scripts/eval_crossmic.py) found no
separate value needed (0.5, as voice_match: 1.0% false accepts, 3.7% misses on 2-3 s).

Bounded refinement (A-21). Speech that fusion harvests from a saved person (a confident face
match, the lip-synced talker, >= harvest_after_s) adapts their print to the glasses mic when:
only one face was talking (`talkers == 1`), at least `adapt_min_s` of it is voiced, it scores
at least `adapt_min` against that person's base print (the bank never vouches for itself, so
it can't drift) and no other saved person scores higher, and no adaptation for them happened
in the last `adapt_gap_s`. Its embedding joins a bank of at most `adapt_max_prints` glasses
prints (the oldest leaves first). A person's score is the better of their base print and the
mean of their bank, the bank's compared with `bank_match` (a max of two scores lets in a few
more impostors, so its line is a little higher). The base print is never replaced; the bank
is saved with it (`adapt_persist`) and deleted with the person.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

STATION = "station"
GLASSES = "glasses"


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

    BLOCK = 32000  # 2 s at 16 kHz

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        # A-21: with this CAM++ export (sherpa-onnx 1.13), audio just past a whole number of
        # 2 s blocks gets a large extra component that every talker shares: two different
        # voices cut to 2.25 s scored 0.90, the same clips at 2.0 s 0.30. Repeating the audio
        # up to a whole number of blocks removes it (0.31 at every length); the same speaker
        # keeps ~0.99 against itself at any length.
        samples = np.asarray(samples, dtype=np.float32)
        if len(samples):
            samples = np.resize(samples, -(-len(samples) // self.BLOCK) * self.BLOCK)
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=samples)
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            raise ValueError("not enough voice audio")
        return np.asarray(self.extractor.compute(stream), dtype=np.float32)


def _consented_at(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def unit(vector) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    if v.ndim != 1 or not np.all(np.isfinite(v)) or norm == 0:
        raise ValueError("invalid voice embedding")
    return v / norm


def voice_path(root: Path, person_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", person_id):
        raise ValueError("invalid person identifier")
    path = root / person_id / "voice.json"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("voice path escapes people directory")
    return path


def write_print(
    root: Path,
    person_id: str,
    vector: np.ndarray,
    consent_t: float,
    source: str = GLASSES,
    adapted: list[np.ndarray] | None = None,
) -> Path:
    """Atomically write a consented voice print (prints only, never audio)."""
    if not _consented_at(consent_t):
        raise ValueError("consent is required")
    path = voice_path(root, person_id)
    record = {
        "consent": True,
        "consent_t": consent_t,
        "source": source,
        "embedding": unit(vector).tolist(),
        "adapted": [unit(v).tolist() for v in adapted or []],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record))
    temporary.replace(path)
    return path


class VoicePrints:
    """Persistent prints require explicit consent; harvested updates stay in RAM."""

    def __init__(
        self,
        root: Path,
        extract: Callable,
        threshold: float,
        enroll_s: float,
        match_s: float,
        options: dict | None = None,
    ):
        self.root, self.extract, self.threshold = Path(root), extract, threshold
        self.enroll_s, self.match_s = enroll_s, match_s
        opts = dict(options or {})
        self.station_threshold = float(opts.get("station_match", threshold))
        self.bank_threshold = float(opts.get("bank_match", threshold))
        self.adapt_min = float(opts.get("adapt_min", threshold))
        self.adapt_max = int(opts.get("adapt_max_prints", 8))
        self.adapt_min_s = float(opts.get("adapt_min_s", 1.0))
        self.adapt_gap_s = float(opts.get("adapt_gap_s", 5.0))
        self.adapt_persist = bool(opts.get("adapt_persist", True))
        self.clock = time.monotonic
        self.enrolled: dict[str, np.ndarray] = {}  # base print per saved person
        self.sources: dict[str, str] = {}
        self.consents: dict[str, float] = {}
        self.adapted: dict[str, list[np.ndarray]] = {}  # glasses-mic bank per saved person
        self._last_adapt: dict[str, float] = {}
        self.session: dict[str, np.ndarray] = {}
        if self.root.exists():
            for path in self.root.glob("*/voice.json"):
                self.load(path.parent.name)

    # ------------------------------------------------------------------ storage
    def load(self, person_id: str) -> bool:
        """(Re)read one person's consented print from disk; False (and forget it) if none."""
        try:
            path = voice_path(self.root, person_id)
            data = json.loads(path.read_text())
            if data.get("consent") is not True or not _consented_at(data.get("consent_t")):
                raise ValueError("no consent")
            base = unit(data["embedding"])
            bank = [unit(v) for v in data.get("adapted") or []][-self.adapt_max :]
            source = STATION if data.get("source") == STATION else GLASSES
        except FileNotFoundError:
            self._drop(person_id)
            return False
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            logger.warning("Ignoring an invalid local voice enrollment")
            self._drop(person_id)
            return False
        self.enrolled = {**self.enrolled, person_id: base}  # copy-on-write for readers
        self.sources[person_id] = source
        self.consents[person_id] = float(data["consent_t"])
        self.adapted[person_id] = bank
        return True

    def _drop(self, person_id: str) -> None:
        self.enrolled = {k: v for k, v in self.enrolled.items() if k != person_id}
        for table in (self.sources, self.consents, self.adapted, self._last_adapt):
            table.pop(person_id, None)

    def enroll(
        self,
        person_id: str,
        samples: np.ndarray,
        consent: bool,
        consent_t: float,
        *,
        guard: Callable | None = None,
        lock=None,
        source: str = GLASSES,
    ) -> None:
        """Save only the embedding from sufficient consented speech."""
        if (
            consent is not True
            or not _consented_at(consent_t)
            or len(samples) < self.enroll_s * 16000
        ):
            raise ValueError("consent and sufficient speech are required")
        voice_path(self.root, person_id)
        vector = unit(self.extract(samples))
        with lock if lock is not None else nullcontext():
            if guard is not None and not guard():
                return
            write_print(self.root, person_id, vector, consent_t, source)
            self.enrolled = {**self.enrolled, person_id: vector}
            self.sources[person_id] = source
            self.consents[person_id] = float(consent_t)
            self.adapted[person_id] = []

    # ------------------------------------------------------------------ scoring
    def _base_scores(self, vector: np.ndarray) -> dict[str, float]:
        """Every saved person's base-print score on the common scale."""
        shift = self.threshold - self.station_threshold
        return {
            key: float(np.dot(vector, base)) + (shift if self.sources.get(key) == STATION else 0.0)
            for key, base in self.enrolled.items()
        }

    def _person_scores(self, vector: np.ndarray) -> dict[str, float]:
        """Every saved person's score on the common scale (see the module docstring)."""
        out = self._base_scores(vector)
        shift = self.threshold - self.bank_threshold
        for key, bank in self.adapted.items():
            if bank and key in out:
                out[key] = max(out[key], float(np.dot(vector, unit(np.mean(bank, axis=0)))) + shift)
        return out

    def scores(self, vector: np.ndarray) -> dict[str, float]:
        out = self._person_scores(vector)
        for key, value in self.session.items():
            if key not in out:
                out[key] = float(np.dot(vector, value))
        return out

    def match(self, samples: np.ndarray) -> tuple[str | None, float]:
        """Return no identity below the configured similarity or duration gate."""
        if len(samples) < self.match_s * 16000 or not (self.enrolled or self.session):
            return None, 0.0
        vector = unit(self.extract(samples))
        score, person = max((s, k) for k, s in self.scores(vector).items())
        return (person if score >= self.threshold else None), score

    # ------------------------------------------------------------------ learning
    def harvest(self, person_id: str, samples: np.ndarray, talkers: int = 1) -> str:
        """Learn from speech fusion attributed to `person_id`; returns what happened.

        Saved people: the bounded refinement ("adapted", or why not). Everyone else
        (strangers "track-N", session names, "offscreen-N"): a session-only print.
        """
        if person_id in self.enrolled:
            return self._adapt(person_id, samples, talkers)
        if len(samples) < self.match_s * 16000:
            return "short"
        vector = unit(self.extract(samples))
        prior = self.session.get(person_id)
        self.session[person_id] = unit(vector + prior) if prior is not None else vector
        return "session"

    def _adapt(self, person_id: str, samples: np.ndarray, talkers: int) -> str:
        if talkers != 1:
            return "several talkers"
        if len(samples) < self.adapt_min_s * 16000:
            return "short"
        now = self.clock()
        if now - self._last_adapt.get(person_id, -math.inf) < self.adapt_gap_s:
            return "too soon"
        vector = unit(self.extract(samples))
        mine = self._base_scores(vector).get(person_id, -1.0)
        if mine < self.adapt_min:
            return "not like their print"
        scores = self._person_scores(vector)
        if any(s > max(mine, scores[person_id]) for k, s in scores.items() if k != person_id):
            return "closer to someone else"
        bank = (self.adapted.get(person_id) or []) + [vector]
        self.adapted[person_id] = bank[-self.adapt_max :]
        self._last_adapt[person_id] = now
        if self.adapt_persist and person_id in self.consents:
            try:
                write_print(
                    self.root,
                    person_id,
                    self.enrolled[person_id],
                    self.consents[person_id],
                    self.sources.get(person_id, GLASSES),
                    self.adapted[person_id],
                )
            except (OSError, ValueError):
                logger.warning("Could not save an adapted voice print")
        logger.info(
            "Voice print of %s adapted to the glasses mic (%d in bank, score %.2f)",
            person_id,
            len(self.adapted[person_id]),
            mine,
        )
        return "adapted"

    # ------------------------------------------------------------------ removal
    def delete(self, person_id: str) -> None:
        """Remove this section's persistent and session data for one person."""
        path = voice_path(self.root, person_id)
        self._drop(person_id)
        self.session.pop(person_id, None)
        path.unlink(missing_ok=True)

    def forget(self) -> None:
        """Retain consented enrollment; wipe all harvested session vectors."""
        self.session.clear()
