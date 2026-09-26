"""ElevenLabs low-latency streaming voice (TODO H-07).

The key comes only from ``.env`` (``ELEVENLABS_API_KEY``, optional ``ELEVENLABS_VOICE_ID``)
typed by a human. It is never logged or echoed. Only the typed reply text is sent.
Audio is requested as raw 16-bit PCM so the first chunk can be played immediately.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "eleven_flash_v2_5"  # lowest-latency ElevenLabs model
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # a premade library voice ("George")
SAMPLE_RATE = 24000


def load_credentials() -> tuple[str | None, str | None]:
    """Read the key and voice id from the environment or the nearest ``.env``.

    Returns (key or None, voice id or None). Never logs the values.
    """

    def clean(value: str | None) -> str | None:
        return value.strip() or None if value else None

    key = clean(os.environ.get("ELEVENLABS_API_KEY"))
    voice = clean(os.environ.get("ELEVENLABS_VOICE_ID"))
    if not key or not voice:
        try:
            from dotenv import dotenv_values, find_dotenv

            path = find_dotenv(usecwd=True)
            if path:
                values = dotenv_values(path)
                key = key or clean(values.get("ELEVENLABS_API_KEY"))
                voice = voice or clean(values.get("ELEVENLABS_VOICE_ID"))
        except Exception:  # noqa: BLE001 - dotenv missing or unreadable .env
            logger.debug("speech_out: .env not readable")
    return key, voice


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        voice_id: str | None = None,
        model_id: str = DEFAULT_MODEL,
        sample_rate: int = SAMPLE_RATE,
        timeout_s: float = 10.0,
    ):
        from elevenlabs.client import ElevenLabs

        self.voice_id = voice_id or DEFAULT_VOICE_ID
        self.model_id = model_id
        self.sample_rate = sample_rate
        self._client = ElevenLabs(api_key=api_key, timeout=timeout_s)

    @classmethod
    def from_env(cls, cfg: dict) -> ElevenLabsTTS | None:
        """Build the client only when a key is present; otherwise None (Kokoro only)."""
        if not cfg.get("elevenlabs", True):
            return None
        key, voice = load_credentials()
        if not key:
            logger.info("speech_out: no ElevenLabs key in .env; offline voice only")
            return None
        return cls(
            key,
            cfg.get("elevenlabs_voice_id") or voice,
            cfg.get("elevenlabs_model", DEFAULT_MODEL),
            int(cfg.get("elevenlabs_sample_rate", SAMPLE_RATE)),
            float(cfg.get("elevenlabs_timeout_s", 10.0)),
        )

    def stream(
        self, text: str, lang: str | None = None, cancel: threading.Event | None = None
    ) -> Iterator[np.ndarray]:
        """Yield mono float32 chunks as they arrive from the network."""
        if cancel is not None and cancel.is_set():
            return
        kwargs = {
            "text": text,
            "model_id": self.model_id,
            "output_format": f"pcm_{self.sample_rate}",
        }
        if lang and lang not in ("en", "und"):
            kwargs["language_code"] = lang
        response = self._client.text_to_speech.stream(voice_id=self.voice_id, **kwargs)
        leftover = b""
        try:
            for chunk in response:
                if cancel is not None and cancel.is_set():
                    break
                if not chunk:
                    continue
                data = leftover + chunk
                usable = len(data) - (len(data) % 2)
                leftover = data[usable:]
                if usable:
                    pcm = np.frombuffer(data[:usable], dtype="<i2").astype(np.float32)
                    yield pcm / 32768.0
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - cleanup must not expose request details
                    logger.debug("speech_out: stream cleanup failed")
