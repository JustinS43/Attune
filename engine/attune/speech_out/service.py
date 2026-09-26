"""SpeechOutService: speak a typed reply (TODO H-09).

On command ``speak`` {text, source}: ElevenLabs first; if it has produced no audio within
``speech_out.fallback_after_s`` (1.5 s) or fails, Kokoro speaks instead. Publishes
``speech_out.playing`` {state: start|end, t} around playback (Section 2 mutes mic
captions until end + 0.5 s) and ``reply.spoken`` {text, voice, t}. One utterance at a
time, in order; ``session.forget`` (or command ``speak.stop``) cuts the current one and
empties the queue.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections.abc import Iterator

from attune.hardware.common import fields, section, shared_clock

from .elevenlabs_tts import ElevenLabsTTS
from .kokoro_tts import KokoroTTS
from .player import SoundDevicePlayer

logger = logging.getLogger(__name__)

_END = object()


class SpeechOutService:
    part = "speech_out"

    def __init__(self, bus, config, *, elevenlabs=None, kokoro=None, player=None):
        self.bus = bus
        self.config = config
        self.cfg = section(config, "speech_out")
        self.fallback_after_s = float(self.cfg.get("fallback_after_s", 1.5))
        self.max_chars = int(self.cfg.get("max_chars", 400))
        self.elevenlabs = elevenlabs
        self.kokoro = kokoro
        self.player = player
        self.jobs: queue.Queue = queue.Queue(maxsize=int(self.cfg.get("queue_max", 5)))
        self.cancel = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unsubs: list = []
        self.speaking = False
        self.error = ""
        self.metrics: dict = {
            "spoken": 0,
            "fallbacks": 0,
            "failed": 0,
            "dropped": 0,
            "last_voice": None,
            "first_audio_ms": None,
        }

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.clock = shared_clock(self.config)
        if self.elevenlabs is None:  # pass False to disable
            try:
                self.elevenlabs = ElevenLabsTTS.from_env(self.cfg)
            except Exception as exc:  # noqa: BLE001 - SDK import/setup problem
                logger.warning("speech_out: ElevenLabs unavailable: %s", type(exc).__name__)
                self.elevenlabs = None
        if self.kokoro is None:  # pass False to disable
            kokoro = KokoroTTS.from_config(self.cfg)
            if kokoro.available():
                self.kokoro = kokoro
            else:
                logger.warning("speech_out: Kokoro model missing: %s", kokoro.missing())
        if self.player is None:
            self.player = SoundDevicePlayer(self.cfg.get("device"), self.cfg.get("volume", 1.0))
        for topic, handler in (("command", self._on_command), ("session.forget", self._on_forget)):
            self._unsubs.append(self.bus.subscribe(topic, handler))
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="speech-out", daemon=True)
        self._thread.start()
        if self.kokoro and self.cfg.get("preload_kokoro", True):
            threading.Thread(target=self._preload, name="kokoro-load", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.cancel.set()
        for unsub in self._unsubs:
            if callable(unsub):
                try:
                    unsub()
                except Exception:
                    logger.debug("ignored error", exc_info=True)
        self._unsubs.clear()
        self._drain()
        if self._thread:
            self._thread.join(timeout=1.5)

    def _preload(self) -> None:
        if not callable(getattr(self.kokoro, "load", None)):
            return
        try:
            t0 = time.monotonic()
            self.kokoro.load("en")
            # the first inference is slower (ONNX warm-up): pay it now, silently
            for _ in self.kokoro.stream("Ready.", "en", threading.Event()):
                pass
            self.metrics["kokoro_load_s"] = round(time.monotonic() - t0, 2)
        except Exception as exc:  # noqa: BLE001
            self.error = f"Kokoro failed to load: {exc}"
            logger.warning("speech_out: %s", self.error)

    # ------------------------------------------------------------- bus
    def _on_command(self, event) -> None:
        e = fields(event)
        name, args = e.get("name"), e.get("args") or {}
        if name == "speak":
            text = " ".join(str(args.get("text", "")).split())[: self.max_chars]
            if not text:
                return
            job = {"text": text, "source": args.get("source", "typed"), "lang": args.get("lang")}
            try:
                self.jobs.put_nowait(job)
            except queue.Full:
                self.metrics["dropped"] += 1
                logger.warning("speech_out: queue full; reply dropped")
        elif name == "speak.stop":
            self.interrupt()

    def _on_forget(self, _event=None) -> None:
        self.interrupt()

    def interrupt(self) -> None:
        """Cut the current reply and forget queued ones."""
        self._drain()
        self.cancel.set()

    def _drain(self) -> None:
        while True:
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                return

    # ------------------------------------------------------------- worker
    def _run(self) -> None:
        last_status = 0.0
        while not self._stop.is_set():
            try:
                job = self.jobs.get(timeout=0.2)
            except queue.Empty:
                job = None
            if job is not None:
                self.cancel.clear()
                try:
                    self.speak(job["text"], job.get("lang"))
                except Exception as exc:
                    self.metrics["failed"] += 1
                    self.error = f"speak failed: {type(exc).__name__}"
                    logger.exception("speech_out: speak failed")
            if time.monotonic() - last_status >= 1.0:
                last_status = time.monotonic()
                self.bus.publish("status.part", self.status())

    def speak(self, text: str, lang: str | None = None) -> str | None:
        """Speak one reply (blocking, on the worker thread). Returns the voice used."""
        cancel = self.cancel
        t_request = time.monotonic()
        first, rest, voice, rate = self._first_audio(text, lang, cancel)
        if first is None:
            if not cancel.is_set():
                self.metrics["failed"] += 1
                self.error = "no voice produced audio"
                logger.error("speech_out: no voice available for the reply")
            return None
        self.metrics["first_audio_ms"] = round((time.monotonic() - t_request) * 1000)
        self.metrics["last_voice"] = voice
        started = {"t": None}

        def on_start() -> None:
            started["t"] = self.clock()
            self.speaking = True
            self.bus.publish("speech_out.playing", {"state": "start", "t": started["t"]})
            self.bus.publish("reply.spoken", {"text": text, "voice": voice, "t": started["t"]})

        try:
            self.player.play(itertools.chain([first], rest), rate, cancel, on_start)
        finally:
            if started["t"] is not None:
                self.speaking = False
                self.bus.publish("speech_out.playing", {"state": "end", "t": self.clock()})
        if started["t"] is not None:
            self.metrics["spoken"] += 1
            self.error = ""
        return voice

    def _first_audio(self, text, lang, cancel):
        """(first chunk, rest iterator, voice, sample rate) or (None, ...)."""
        if self.elevenlabs and not cancel.is_set():
            got = self._try_elevenlabs(text, lang, cancel)
            if got is not None:
                return got
            self.metrics["fallbacks"] += 1
        if self.kokoro and not cancel.is_set():
            try:
                chunks = self.kokoro.stream(text, lang, cancel)
                first = next(chunks, None)
                if first is not None:
                    return first, chunks, "kokoro", int(self.kokoro.sample_rate)
            except Exception as exc:  # noqa: BLE001
                self.error = f"Kokoro failed: {exc}"
                logger.warning("speech_out: %s", self.error)
        return None, iter(()), None, 0

    def _try_elevenlabs(self, text, lang, cancel):
        """Start streaming; give up if no audio within fallback_after_s."""
        tts = self.elevenlabs
        chunks: queue.Queue = queue.Queue()
        abandon = threading.Event()

        def produce() -> None:
            try:
                for chunk in tts.stream(text, lang, abandon):
                    if abandon.is_set() or cancel.is_set():
                        break
                    chunks.put(chunk)
            except Exception as exc:  # noqa: BLE001 - network, quota, bad key
                chunks.put(exc)
            finally:
                chunks.put(_END)

        threading.Thread(target=produce, name="elevenlabs-stream", daemon=True).start()
        try:
            first = chunks.get(timeout=self.fallback_after_s)
        except queue.Empty:
            abandon.set()
            logger.warning(
                "speech_out: no ElevenLabs audio within %.1f s; using Kokoro", self.fallback_after_s
            )
            return None
        if first is _END or isinstance(first, Exception):
            abandon.set()
            if isinstance(first, Exception):
                # never include the exception text: it may echo request headers
                logger.warning(
                    "speech_out: ElevenLabs failed (%s); using Kokoro", type(first).__name__
                )
            return None

        def rest() -> Iterator:
            while True:
                item = chunks.get()
                if item is _END:
                    return
                if isinstance(item, Exception):
                    logger.warning("speech_out: ElevenLabs stream broke (%s)", type(item).__name__)
                    return
                if cancel.is_set():
                    abandon.set()
                    return
                yield item

        return first, rest(), "elevenlabs", int(getattr(tts, "sample_rate", 24000))

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        voices = [n for n, v in (("elevenlabs", self.elevenlabs), ("kokoro", self.kokoro)) if v]
        ok = bool(voices) and not self.error
        detail = self.error or ("speaking" if self.speaking else f"ready: {', '.join(voices)}")
        if not voices:
            detail = "no voice available (no ElevenLabs key and no Kokoro model)"
        return {
            "part": self.part,
            "ok": ok,
            "detail": detail,
            "metrics": {**self.metrics, "voices": voices, "queued": self.jobs.qsize()},
        }
