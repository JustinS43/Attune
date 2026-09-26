"""Offline voice: Kokoro-82M (multi-lang v1.0) via sherpa-onnx (TODO H-08).

Model folder: ``speech_out.kokoro_dir`` (default ``models/tts/kokoro-multi-lang-v1_0``)
with ``model.onnx``, ``voices.bin``, ``tokens.txt``, ``espeak-ng-data/`` and the
lexicons. Speaker ids come from the model's metadata: ``af_heart`` = 3 (English),
``ef_dora`` = 28 (Spanish). Also usable by scripts to make test speech.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Iterator
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_DIR = "models/tts/kokoro-multi-lang-v1_0"
REQUIRED = ("model.onnx", "voices.bin", "tokens.txt", "espeak-ng-data")
_DONE = object()


class KokoroTTS:
    name = "kokoro"

    def __init__(
        self,
        model_dir: str | Path = DEFAULT_DIR,
        sid: int = 3,
        sid_es: int = 28,
        speed: float = 1.0,
        num_threads: int = 4,
        provider: str = "cpu",
    ):
        self.model_dir = Path(model_dir)
        self.sid, self.sid_es, self.speed = int(sid), int(sid_es), float(speed)
        self.num_threads, self.provider = int(num_threads), provider
        self._engines: dict[str, object] = {}
        self._lock = threading.Lock()
        self.sample_rate = 24000

    @classmethod
    def from_config(cls, cfg: dict) -> KokoroTTS:
        return cls(
            cfg.get("kokoro_dir", DEFAULT_DIR),
            cfg.get("kokoro_sid", 3),
            cfg.get("kokoro_sid_es", 28),
            cfg.get("kokoro_speed", 1.0),
            cfg.get("kokoro_threads", 4),
            cfg.get("kokoro_provider", "cpu"),
        )

    def missing(self) -> list[str]:
        missing = [name for name in REQUIRED if not (self.model_dir / name).exists()]
        if not list(self.model_dir.glob("lexicon*.txt")):
            missing.append("lexicon*.txt")
        return missing

    def available(self) -> bool:
        return not self.missing()

    def _family(self, lang: str | None) -> str:
        return "es" if (lang or "en").lower().startswith("es") else "en"

    def load(self, lang: str | None = None):
        """Create (once) the sherpa-onnx engine for this language family."""
        family = self._family(lang)
        with self._lock:
            if family in self._engines:
                return self._engines[family]
            missing = self.missing()
            if missing:
                raise FileNotFoundError(
                    f"Kokoro model files missing in {self.model_dir}: {missing}"
                )
            import sherpa_onnx

            d = self.model_dir
            if family == "es":
                lexicon, lang_code = "", "es"
            else:
                lexicons = [d / "lexicon-us-en.txt", d / "lexicon-zh.txt"]
                lexicon = ",".join(str(x) for x in lexicons if x.exists())
                lang_code = ""
            kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(d / "model.onnx"),
                voices=str(d / "voices.bin"),
                tokens=str(d / "tokens.txt"),
                lexicon=lexicon,
                data_dir=str(d / "espeak-ng-data"),
                lang=lang_code,
            )
            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    kokoro=kokoro, num_threads=self.num_threads, provider=self.provider
                ),
                max_num_sentences=1,
            )
            if not config.validate():
                raise RuntimeError("invalid Kokoro config")
            engine = sherpa_onnx.OfflineTts(config)
            self.sample_rate = int(engine.sample_rate)
            self._engines[family] = engine
            logger.info("speech_out: Kokoro loaded (%s, %d Hz)", family, self.sample_rate)
            return engine

    def stream(
        self, text: str, lang: str | None = None, cancel: threading.Event | None = None
    ) -> Iterator[np.ndarray]:
        """Yield float32 chunks (one per sentence) while synthesis continues."""
        engine = self.load(lang)
        sid = self.sid_es if self._family(lang) == "es" else self.sid
        chunks: queue.Queue = queue.Queue()

        def callback(samples, _progress) -> int:
            chunks.put(np.array(samples, dtype=np.float32, copy=True))
            return 1 if cancel is not None and cancel.is_set() else 0

        def run() -> None:
            try:
                engine.generate(text, sid=sid, speed=self.speed, callback=callback)
            except Exception as exc:  # noqa: BLE001
                chunks.put(exc)
            finally:
                chunks.put(_DONE)

        threading.Thread(target=run, name="kokoro-synth", daemon=True).start()
        while True:
            item = chunks.get()
            if item is _DONE:
                return
            if isinstance(item, Exception):
                raise item
            if cancel is not None and cancel.is_set():
                continue  # drain until the synth thread finishes
            if len(item):
                yield item

    def synthesize(self, text: str, lang: str | None = None) -> tuple[np.ndarray, int]:
        """Whole utterance at once (for scripts making test speech)."""
        audio = list(self.stream(text, lang))
        samples = np.concatenate(audio) if audio else np.zeros(0, np.float32)
        return samples, self.sample_rate
