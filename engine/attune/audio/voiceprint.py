"""CAM++ embeddings; manual or automatic disk prints and forgettable session prints.

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

import hashlib
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
        self.dim = int(self.extractor.dim)  # the size of every print this model makes
        self.model_id = model_tag(path)  # stored with each print (A-27)

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


def model_tag(path: str | Path) -> str:
    """A short fingerprint of a voice model file: prints of different models never match
    even when they have the same size (A-27)."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def _valid_stamp(value) -> bool:
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
    automatic: bool = False,
    model: str | None = None,
) -> Path:
    """Atomically write a voice print (prints only, never audio)."""
    if not _valid_stamp(consent_t):
        raise ValueError("a capture timestamp is required")
    path = voice_path(root, person_id)
    record = {
        "consent": not automatic,
        "automatic": automatic,
        "consent_t": consent_t,
        "source": source,
        "embedding": unit(vector).tolist(),
        "adapted": [unit(v).tolist() for v in adapted or []],
    }
    if model:
        record["model"] = model  # which voice model made it (A-27)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record))
    temporary.replace(path)
    return path


class VoicePrints:
    """Manual and automatic prints persist; other harvested voices stay in RAM."""

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
        self.tiers: dict[str, str] = {}
        self._match_tiers: dict[
            str, tuple[np.ndarray, list[str], np.ndarray, np.ndarray, float]
        ] = {}
        # A-27: prints made by another voice model (another size) are ignored, not
        # compared; the extractor tells the size, else the first voice heard does.
        self.dim: int | None = getattr(extract, "dim", None)
        self.model_id: str | None = getattr(extract, "model_id", None)
        self._foreign: set[str] = set()
        if self.root.exists():
            for path in self.root.glob("*/voice.json"):
                self.load(path.parent.name)

    def refresh_tier(self, person_id: str) -> None:
        """Refresh a saved person's priority when Vision changes their People page."""
        try:
            meta = json.loads((self.root / person_id / "meta.json").read_text())
            tier = meta.get("tier", "other")
        except (OSError, ValueError, TypeError):
            tier = "other"
        self.tiers[person_id] = tier if tier in {"close", "familiar", "other"} else "other"
        self._rebuild_match_index()

    def _rebuild_match_index(self) -> None:
        """Group base and adapted vectors into three small, vectorized search tables."""
        lengths = {len(v) for v in (*self.enrolled.values(), *self.session.values())}
        if len(lengths) > 1 and self.dim is None:
            # Legacy files may use different models; the first live vector
            # establishes the active size and _learn_dim filters them then.
            self._match_tiers = {}
            return
        grouped: dict[str, list[tuple[str, np.ndarray, float]]] = {}
        for pid, base in self.enrolled.items():
            tier = self.tiers.get(pid, "other")
            shift = (
                self.threshold - self.station_threshold if self.sources.get(pid) == STATION else 0.0
            )
            grouped.setdefault(tier, []).append((pid, base, shift))
            bank = self.adapted.get(pid) or []
            if bank:
                grouped[tier].append(
                    (pid, unit(np.mean(bank, axis=0)), self.threshold - self.bank_threshold)
                )
        for pid, vector in self.session.items():
            grouped.setdefault("other", []).append((pid, vector, 0.0))
        self._match_tiers = {}
        for tier, rows in grouped.items():
            matrix = np.stack([vector for _, vector, _ in rows])
            center = np.mean(matrix, axis=0)
            radius = float(np.max(np.linalg.norm(matrix - center, axis=1)))
            self._match_tiers[tier] = (
                matrix,
                [pid for pid, _, _ in rows],
                np.asarray([bias for _, _, bias in rows], dtype=np.float32),
                center,
                radius,
            )

    def _score_tier(self, tier: str, vector: np.ndarray):
        matrix, owners, biases, _, _ = self._match_tiers[tier]
        return zip(owners, matrix @ vector + biases)

    # ------------------------------------------------------------------ storage
    def load(self, person_id: str) -> bool:
        """(Re)read one person's persistent print; False (and forget it) if none."""
        try:
            path = voice_path(self.root, person_id)
            data = json.loads(path.read_text())
            if not (
                data.get("consent") is True or data.get("automatic") is True
            ) or not _valid_stamp(data.get("consent_t")):
                raise ValueError("no valid capture record")
            base = unit(data["embedding"])
            bank = [unit(v) for v in data.get("adapted") or []][-self.adapt_max :]
            source = STATION if data.get("source") == STATION else GLASSES
            tag = data.get("model")
            if (self.dim is not None and len(base) != self.dim) or (
                self.model_id and tag and tag != self.model_id
            ):
                self._ignore_foreign(person_id, len(base))
                return False
            bank = [v for v in bank if len(v) == len(base)]
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
        self.refresh_tier(person_id)
        return True

    def remember_auto(self, person_id: str, session_id: str) -> bool:
        """Persist an already harvested voice vector for an automatic face profile."""
        vector = self.session.get(session_id)
        if vector is None or person_id in self.enrolled:
            return False
        stamp = time.time()
        write_print(
            self.root, person_id, vector, stamp, GLASSES, automatic=True, model=self.model_id
        )
        self.session.pop(session_id, None)
        self.enrolled = {**self.enrolled, person_id: vector}
        self.sources[person_id] = GLASSES
        self.consents[person_id] = stamp
        self.adapted[person_id] = []
        self.refresh_tier(person_id)
        return True

    def _ignore_foreign(self, person_id: str, size: int) -> None:
        """Forget (in memory only) a print from another voice model; the file stays, so
        switching the model back brings it back."""
        if person_id not in self._foreign:
            logger.warning(
                "voice print for %s is from another voice model (%d values; this one makes"
                " %s, model %s); re-enroll",
                person_id,
                size,
                self.dim,
                self.model_id,
            )
            self._foreign.add(person_id)
        self._drop(person_id)

    def _learn_dim(self, vector: np.ndarray) -> np.ndarray:
        """The first voice heard tells the model's print size: drop prints of another."""
        if self.dim is None:
            self.dim = len(vector)
            for key, base in list(self.enrolled.items()):
                if len(base) != self.dim:
                    self._ignore_foreign(key, len(base))
            self.session = {k: v for k, v in self.session.items() if len(v) == self.dim}
            self._rebuild_match_index()
        return vector

    def _drop(self, person_id: str) -> None:
        self.enrolled = {k: v for k, v in self.enrolled.items() if k != person_id}
        for table in (self.sources, self.consents, self.adapted, self._last_adapt):
            table.pop(person_id, None)
        self.tiers.pop(person_id, None)
        self._rebuild_match_index()

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
            or not _valid_stamp(consent_t)
            or len(samples) < self.enroll_s * 16000
        ):
            raise ValueError("consent and sufficient speech are required")
        voice_path(self.root, person_id)
        vector = self._learn_dim(unit(self.extract(samples)))
        with lock if lock is not None else nullcontext():
            if guard is not None and not guard():
                return
            write_print(self.root, person_id, vector, consent_t, source, model=self.model_id)
            self.enrolled = {**self.enrolled, person_id: vector}
            self.sources[person_id] = source
            self.consents[person_id] = float(consent_t)
            self.adapted[person_id] = []
            self.refresh_tier(person_id)

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
        vector = self._learn_dim(unit(self.extract(samples)))
        if not (self.enrolled or self.session):
            return None, 0.0
        order = [tier for tier in ("close", "familiar", "other") if tier in self._match_tiers]
        score, person = -math.inf, None
        query_norm = float(np.linalg.norm(vector))
        for index, tier in enumerate(order):
            for pid, value in self._score_tier(tier, vector):
                if value > score or (value == score and person is not None and pid > person):
                    score, person = float(value), pid
            remaining = order[index + 1 :]
            if score >= self.threshold and remaining:
                bound = max(
                    float(np.dot(vector, self._match_tiers[name][3]))
                    + self._match_tiers[name][4] * query_norm
                    + float(np.max(self._match_tiers[name][2]))
                    for name in remaining
                )
                if bound + 1e-6 < score:
                    break
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
        vector = self._learn_dim(unit(self.extract(samples)))
        prior = self.session.get(person_id)
        self.session[person_id] = unit(vector + prior) if prior is not None else vector
        self._rebuild_match_index()
        return "session"

    def _adapt(self, person_id: str, samples: np.ndarray, talkers: int) -> str:
        if talkers != 1:
            return "several talkers"
        if len(samples) < self.adapt_min_s * 16000:
            return "short"
        now = self.clock()
        if now - self._last_adapt.get(person_id, -math.inf) < self.adapt_gap_s:
            return "too soon"
        vector = self._learn_dim(unit(self.extract(samples)))
        mine = self._base_scores(vector).get(person_id, -1.0)
        if mine < self.adapt_min:
            return "not like their print"
        scores = self._person_scores(vector)
        if any(s > max(mine, scores[person_id]) for k, s in scores.items() if k != person_id):
            return "closer to someone else"
        bank = (self.adapted.get(person_id) or []) + [vector]
        self.adapted[person_id] = bank[-self.adapt_max :]
        self._last_adapt[person_id] = now
        self._rebuild_match_index()
        if self.adapt_persist and person_id in self.consents:
            try:
                write_print(
                    self.root,
                    person_id,
                    self.enrolled[person_id],
                    self.consents[person_id],
                    self.sources.get(person_id, GLASSES),
                    self.adapted[person_id],
                    automatic=person_id.startswith("auto-"),
                    model=self.model_id,
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
        self._rebuild_match_index()
