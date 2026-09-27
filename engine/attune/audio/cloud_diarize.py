"""Cloud captions: Google Speech-to-Text streaming speaker diarization (optional, off by default).

Section 2 - Audio & Language. TODO: A-32. Contracts: "Cloud captions" in docs/contracts.md.
Why this API and model, and how fusion uses the tags: docs/cloud-diarization.md.

While the wearer has turned cloud captions on (`cloud.set`), the 16 kHz mic audio that the
local captions hear is also streamed to Google Speech-to-Text v1 `StreamingRecognize` with
speaker diarization, and every word that comes back with a speaker tag is published as
`speaker.cloud` (engine-clock times). The local captions keep running regardless: this
service only adds "who is speaking" evidence, and says how usable it is in `cloud.state`.

Threads (the bus callbacks only put items in bounded queues and return):
- `cloud-pump` owns the state: it turns 10 ms blocks into 100 ms LINEAR16 chunks, opens,
  feeds and restarts streams, watches latency, and publishes `cloud.state` / `status.part`.
- one `cloud-stream-N` per Google stream reads its responses and publishes `speaker.cloud`.

Off (the default), paused, or without credentials: no Google client is created and the
audio callback drops every block at once, so nothing is queued or sent. During our own spoken
reply the stream gets silence instead of the audio. Credentials come only from the
environment or `.env` (`GOOGLE_SPEECH_API_KEY` first, else `GOOGLE_APPLICATION_CREDENTIALS`),
are re-read when they change (a key saved in Settings applies without a restart), and are
never logged: the log says only "cloud: credentials present" / "cloud: credentials missing".
Google's own error text is never logged either, only the error's class name.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import numpy as np

from .runtime import engine_clock
from .runtime import fields as event_fields

logger = logging.getLogger(__name__)

SPEAKER_CLOUD = "speaker.cloud"
CLOUD_STATE = "cloud.state"
KEY_VAR = "GOOGLE_SPEECH_API_KEY"
SERVICE_VAR = "GOOGLE_APPLICATION_CREDENTIALS"
RATE = 16000
SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
_AUTH = {"Unauthenticated", "PermissionDenied", "Unauthorized", "Forbidden"}
_QUOTA = {"ResourceExhausted", "TooManyRequests"}
_LIMIT = {"OutOfRange"}  # "Exceeded maximum allowed stream duration": just start a new stream
_BAD_REQUEST = {"InvalidArgument", "BadRequest", "FailedPrecondition", "NotFound"}
_GRPC_KIND = {16: "auth", 7: "auth", 8: "quota", 11: "limit", 3: "error", 9: "error"}


@dataclass
class CloudSettings:
    """The [cloud] keys this service uses (fusion reads its own keys from the same table)."""

    enabled: bool = False
    remember: bool = True
    provider: str = "google"
    language: str = "en-US"
    models: dict[str, str] = field(
        default_factory=lambda: {"en-US": "latest_long", "es-US": "command_and_search"}
    )
    min_speakers: int = 2
    max_speakers: int = 6
    interim_results: bool = True
    endpoint: str = ""
    chunk_ms: int = 100
    queue_s: float = 5.0
    stream_max_s: float = 290.0
    overlap_s: float = 3.0
    latency_fallback_ms: float = 2500.0
    latency_window: int = 5
    stall_s: float = 4.0
    retry_s: list[float] = field(default_factory=lambda: [2.0, 5.0, 15.0, 30.0, 60.0])
    quota_retry_s: float = 300.0
    connect_grace_s: float = 2.0  # an open stream without an error this long counts as on
    idle_close_s: float = 5.0  # no audio arriving this long: close the stream (Google times out)
    credentials_poll_s: float = 2.0  # how often .env is checked for a new or changed key

    @classmethod
    def from_config(cls, table: dict | None) -> CloudSettings:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (table or {}).items() if k in known})

    @property
    def languages(self) -> list[str]:
        return list(self.models)

    def model_for(self, language: str) -> str:
        return self.models.get(language, "latest_long")


# ---------------------------------------------------------------------------- credentials
@dataclass(frozen=True)
class Credentials:
    """Where the Google credentials are; `repr` never shows a value."""

    api_key: str | None = field(default=None, repr=False)
    service_file: str | None = field(default=None, repr=False)

    @property
    def present(self) -> bool:
        return bool(self.api_key or self.service_file)

    def kinds(self) -> list[str]:
        """The ways to sign in, in the order they are tried: the API key first."""
        return [k for k, v in (("api_key", self.api_key), ("service", self.service_file)) if v]

    def fingerprint(self) -> str:
        """Changes when a credential changes (compared in memory only, never logged)."""
        raw = f"{self.api_key or ''}\0{self.service_file or ''}".encode()
        return hashlib.sha256(raw).hexdigest()


def read_credentials(environ: dict | None = None, dotenv_path: str | None = None) -> Credentials:
    """The key or service-account file from the environment, else the nearest `.env`.

    A service-account path counts only when that file exists. Never logs the values.
    """
    env = os.environ if environ is None else environ

    def clean(value: Any) -> str | None:
        return str(value).strip() or None if value else None

    key, path = clean(env.get(KEY_VAR)), clean(env.get(SERVICE_VAR))
    if not key or not path:
        try:
            from dotenv import dotenv_values, find_dotenv

            found = dotenv_path if dotenv_path is not None else find_dotenv(usecwd=True)
            if found:
                values = dotenv_values(found)
                key = key or clean(values.get(KEY_VAR))
                path = path or clean(values.get(SERVICE_VAR))
        except Exception:  # noqa: BLE001 - python-dotenv missing or an unreadable .env
            logger.debug("cloud: .env not readable")
    if path and not Path(path).is_file():
        path = None
    return Credentials(key, path)


# ---------------------------------------------------------------------------- Google API
def _secs(value: Any) -> float:
    """A proto-plus Duration (timedelta), a raw Duration (seconds + nanos), or a number."""
    if value is None:
        return 0.0
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())
    if hasattr(value, "seconds"):
        return float(value.seconds) + float(getattr(value, "nanos", 0)) / 1e9
    return float(value)


@dataclass
class Result:
    """One streaming result, times in seconds from the start of its stream."""

    final: bool
    words: list[tuple[str, float, float, str]]  # (word, t0, t1, tag); tag "" = none
    end: float
    confidence: float | None = None
    lang: str = ""


def parse_response(resp: Any) -> list[Result]:
    """The results of one StreamingRecognizeResponse (top alternative only)."""
    out = []
    for result in getattr(resp, "results", None) or []:
        alternatives = getattr(result, "alternatives", None) or []
        if not alternatives:
            continue
        top = alternatives[0]
        words = []
        for w in getattr(top, "words", None) or []:
            label = str(getattr(w, "speaker_label", "") or "")
            if not label:
                tag = int(getattr(w, "speaker_tag", 0) or 0)
                label = str(tag) if tag > 0 else ""
            words.append(
                (str(w.word), _secs(getattr(w, "start_time", 0)), _secs(w.end_time), label)
            )
        final = bool(getattr(result, "is_final", False))
        conf = getattr(top, "confidence", None) if final else None
        out.append(
            Result(
                final,
                words,
                _secs(getattr(result, "result_end_time", 0)),
                float(conf) if conf else None,
                str(getattr(result, "language_code", "") or ""),
            )
        )
    return out


class GoogleApi:
    """The google-cloud-speech v1 calls, imported only when a stream opens."""

    def available(self) -> bool:
        try:
            from google.cloud import speech_v1  # noqa: F401
        except Exception:  # noqa: BLE001 - not installed, or broken
            return False
        return True

    def client(self, creds: Credentials, kind: str, settings: CloudSettings) -> Any:
        from google.api_core.client_options import ClientOptions
        from google.cloud import speech_v1

        options: dict[str, Any] = {}
        if settings.endpoint:
            options["api_endpoint"] = settings.endpoint
        if kind == "api_key":
            # sent as the x-goog-api-key metadata on the gRPC call
            return speech_v1.SpeechClient(
                client_options=ClientOptions(api_key=creds.api_key, **options)
            )
        from google.oauth2 import service_account

        sa = service_account.Credentials.from_service_account_file(
            creds.service_file, scopes=list(SCOPES)
        )
        return speech_v1.SpeechClient(
            credentials=sa, client_options=ClientOptions(**options) if options else None
        )

    def streaming_config(self, settings: CloudSettings, language: str) -> Any:
        from google.cloud import speech_v1

        config = speech_v1.RecognitionConfig(
            encoding=speech_v1.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=RATE,
            audio_channel_count=1,
            language_code=language,
            model=settings.model_for(language),
            enable_word_time_offsets=True,
            enable_automatic_punctuation=False,
            diarization_config=speech_v1.SpeakerDiarizationConfig(
                enable_speaker_diarization=True,
                min_speaker_count=int(settings.min_speakers),
                max_speaker_count=int(settings.max_speakers),
            ),
        )
        return speech_v1.StreamingRecognitionConfig(
            config=config, interim_results=bool(settings.interim_results)
        )

    def request(self, chunk: bytes) -> Any:
        from google.cloud import speech_v1

        return speech_v1.StreamingRecognizeRequest(audio_content=chunk)

    def recognize(self, client: Any, config: Any, requests: Iterable[Any]) -> Iterator[Any]:
        return client.streaming_recognize(config=config, requests=requests)


def error_kind(exc: BaseException) -> str:
    """What an error means: auth, quota, limit (just start a new stream), error or network."""
    names = {c.__name__ for c in type(exc).__mro__}
    if names & _AUTH:
        return "auth"
    if names & _QUOTA:
        return "quota"
    if names & _LIMIT:
        return "limit"
    if names & _BAD_REQUEST:
        return "error"
    code = getattr(exc, "grpc_status_code", None)
    value = getattr(code, "value", code)
    if isinstance(value, tuple):
        value = value[0]
    if isinstance(value, int) and value in _GRPC_KIND:
        return _GRPC_KIND[value]
    return "network"


# ---------------------------------------------------------------------------- one stream
class _Stream:
    """One Google stream: its audio queue, clock origin and the tags already published."""

    def __init__(self, sid: str, t0: float, max_chunks: int, kind: str, language: str):
        self.id, self.t0, self.kind, self.language = sid, t0, kind, language
        self.q: queue.Queue = queue.Queue(maxsize=max(4, max_chunks))
        self.closing = threading.Event()
        self.cancelled = False
        self.samples = 0  # audio fed, so t0 + samples / RATE is the next chunk's time
        self.opened = 0.0
        self.responses = 0
        self.words: dict[int, tuple[str, str]] = {}  # word start (10 ms) -> (word, tag) sent
        self.error: BaseException | None = None
        self.thread: threading.Thread | None = None
        self.call: Any = None

    def feed(self, chunk: bytes) -> bool:
        try:
            self.q.put_nowait(chunk)
        except queue.Full:
            return False
        self.samples += len(chunk) // 2
        return True

    def close(self) -> None:
        """Half-close: Google finishes the audio it has and ends the stream."""
        self.closing.set()

    def cancel(self) -> None:
        self.cancelled = True
        self.closing.set()
        cancel = getattr(self.call, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:  # noqa: BLE001 - already finished
                logger.debug("cloud: %s was already finished", self.id)

    def chunks(self) -> Iterator[bytes]:
        while True:
            try:
                chunk = self.q.get(timeout=0.1)
            except queue.Empty:
                if self.closing.is_set():
                    return
                continue
            yield chunk


# ---------------------------------------------------------------------------- the service
class CloudDiarizeService:
    """Cloud captions: stream the mic to Google while the wearer has them on."""

    def __init__(
        self,
        bus: Any,
        config: dict,
        *,
        api: Any = None,
        credentials: Callable[[], Credentials] | None = None,
    ):
        self.bus, self.config = bus, config
        self.s = CloudSettings.from_config(config.get("cloud"))
        self.api = api or GoogleApi()
        self.read_credentials = credentials or read_credentials
        engine = config.get("engine") or {}
        self.choice_path = Path(engine.get("data_dir", "data")) / "cloud.json"
        self.mute_after_s = float((config.get("audio") or {}).get("mute_after_reply_s", 0.5))
        self.clock: Callable[[], float] = time.perf_counter
        self.enabled = bool(self.s.enabled)
        self.language = self.s.language if self.s.language in self.s.models else "en-US"
        self.paused = False
        self.muted_until = float("-inf")
        self.accepting = False  # read by the audio callback: queue blocks only when True
        # One queue for audio and controls, so they are handled in the order they happened
        # (a pause, "off" or our own reply always applies to the audio after it). Audio items
        # are capped at queue_s; controls are never dropped.
        self.inbox: queue.SimpleQueue = queue.SimpleQueue()
        self.queued_audio = 0
        self._qlock = threading.Lock()
        self.max_audio = max(10, round(self.s.queue_s * 100))
        self._available: bool | None = None
        self.closed = threading.Event()
        self._subs: list = []
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # pump state
        self.state, self.reason = "off", ""
        self.creds = Credentials()
        self._creds_print: str | None = None
        self._creds_checked = -1e9
        self._creds_logged: bool | None = None
        self._rejected: set[str] = set()  # sign-in kinds Google refused, for these credentials
        self.active: _Stream | None = None
        self.old: list[_Stream] = []
        self._n = 0
        self.restarts = 0
        self.sent_s = 0.0
        self.dropped = 0
        self._chunk: list[np.ndarray] = []
        self._chunk_n = 0
        self._chunk_t: float | None = None  # engine time of the first sample in _chunk
        self._next_t: float | None = None  # where the next block should start
        self._last_audio = -1e9
        self._overlap: deque[tuple[float, bytes]] = deque()
        self._retry_at = -1e9
        self._retries = 0
        self.latencies: deque[float] = deque(maxlen=max(1, int(self.s.latency_window)))
        self._last_response = -1e9
        self._speech_since: float | None = None  # speech heard since the last response
        self._published: tuple | None = None
        self._published_latency: tuple[float, int | None] = (-1e9, None)
        self._status_t = -1e9

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Subscribe and start the pump. Light: Google's library is imported only when a
        stream opens, and nothing is created while cloud captions are off."""
        self.clock = engine_clock(self.config)
        self._load_choice()
        subs = {
            "audio.block": self._on_block,
            "audio.vad": self._on_vad,
            "command": self._on_control("command"),
            "paused": self._on_control("paused"),
            "speech_out.playing": self._on_control("speech_out.playing"),
            "session.forget": self._on_control("session.forget"),
        }
        self._subs = [self.bus.subscribe(topic, cb) for topic, cb in subs.items()]
        self._readiness(self.clock())  # the pages learn the state at once
        self.closed.clear()
        self._thread = threading.Thread(target=self._run, name="cloud-pump", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.closed.set()
        self.accepting = False
        for unsub in self._subs:
            if callable(unsub):
                unsub()
        self._subs = []
        if self._thread:
            self._thread.join(timeout=1.0)
        for s in [self.active, *self.old]:
            if s is not None:
                s.cancel()
        self.active, self.old = None, []

    # ------------------------------------------------------------------ bus callbacks (fast)
    def _on_block(self, ev: Any) -> None:
        if not self.accepting:
            return  # off, paused or no credentials: nothing is queued, nothing leaves
        rate = ev.get("sample_rate") if isinstance(ev, dict) else getattr(ev, "sample_rate", 0)
        if rate != RATE:
            return
        t = ev.get("t") if isinstance(ev, dict) else getattr(ev, "t", None)
        samples = ev.get("samples") if isinstance(ev, dict) else getattr(ev, "samples", None)
        with self._qlock:
            if self.queued_audio >= self.max_audio:
                self.dropped += 1  # the pump sees the gap and pads or restarts the stream
                return
            self.queued_audio += 1
        self.inbox.put(("audio", (float(t), samples)))

    def _on_vad(self, ev: Any) -> None:
        if not self.accepting:
            return
        speech = ev.get("is_speech") if isinstance(ev, dict) else getattr(ev, "is_speech", False)
        if speech:
            with self._lock:
                if self._speech_since is None:
                    self._speech_since = self.clock()

    def _on_control(self, topic: str) -> Callable[[Any], None]:
        def receive(ev: Any) -> None:
            if topic == "command":
                name = ev.get("name") if isinstance(ev, dict) else getattr(ev, "name", None)
                if name != "cloud.set":
                    return
            self.inbox.put((topic, event_fields(ev) if ev is not None else {}))

        return receive

    # ------------------------------------------------------------------ the pump
    def _run(self) -> None:
        while not self.closed.is_set():
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001 - never let the pump die
                logger.error("cloud: pump step failed (%s)", type(exc).__name__)
                time.sleep(0.05)

    def step(self, wait_s: float = 0.05) -> None:
        """One pump round: audio and controls in order, then restarts, health and reports."""
        items: list = []
        try:
            items.append(self.inbox.get(timeout=wait_s))
            while len(items) < 60:
                items.append(self.inbox.get_nowait())
        except queue.Empty:
            pass
        self._readiness(self.clock())
        for topic, ev in items:
            if topic == "audio":
                with self._qlock:
                    self.queued_audio -= 1
                if self.accepting:
                    self._audio(float(ev[0]), ev[1])
            else:
                self._control(topic, ev)
                self._readiness(self.clock())
        self._maintain(self.clock())
        self._report(self.clock())

    def _control(self, topic: str, ev: dict) -> None:
        if topic == "command":
            args = ev.get("args") or {}
            on = args.get("on") if isinstance(args, dict) else None
            language = args.get("language") if isinstance(args, dict) else None
            if not isinstance(on, bool):
                logger.info("cloud: cloud.set needs {on: true|false}; ignored")
                return
            if language is not None and language not in self.s.models:
                logger.info("cloud: cloud.set language %r is not offered; ignored", language)
                language = None
            changed_language = language is not None and language != self.language
            if on == self.enabled and not changed_language:
                self._publish_state(force=True)
                return
            self.enabled = on
            if language is not None:
                self.language = language
            self._save_choice()
            logger.info("cloud: captions turned %s", "on" if on else "off")
            self._retries, self._retry_at, self._rejected = 0, -1e9, set()
            if not on or changed_language:
                self._end_streams(cancel=not on)
        elif topic == "paused":
            self.paused = bool(ev.get("paused"))
            if self.paused:
                self._end_streams(cancel=True)
        elif topic == "speech_out.playing":
            # our own reply: the stream hears silence until it ends (+ mute_after_reply_s)
            if ev.get("state") == "start":
                self.muted_until = float("inf")
            else:
                self.muted_until = float(ev.get("t", self.clock())) + self.mute_after_s
        elif topic == "session.forget":
            # tags heard before must not carry over: a new stream, no overlap
            if self.active is not None:
                self._end_streams(cancel=True)
        elif topic == "stream_done":
            self._stream_done(ev["stream"])

    # ------------------------------------------------------------------ credentials
    def _readiness(self, now: float) -> None:
        """Credentials, library and the wearer's choice: may audio go to the cloud now?"""
        if not self.enabled:
            self.accepting = False
            self._drain()
            self._set_state("off", "")
            return
        wall = time.monotonic()  # a real-time poll, whatever the engine clock is doing
        if wall - self._creds_checked >= self.s.credentials_poll_s or self._creds_print is None:
            self._creds_checked = wall
            creds = self.read_credentials()
            fp = creds.fingerprint()
            if fp != self._creds_print:
                changed = self._creds_print is not None
                self.creds, self._creds_print, self._rejected = creds, fp, set()
                if self._creds_logged is not creds.present:
                    self._creds_logged = creds.present
                    logger.info("cloud: credentials %s", "present" if creds.present else "missing")
                if changed and self.active is not None:
                    self._end_streams(cancel=False)  # the next stream signs in with the new ones
                self._retry_at, self._retries = -1e9, 0
        if self.paused:
            self.accepting = False
            self._drain()
            self._set_state("paused", "")
            return
        if not self.creds.present:
            self._set_state("unavailable", "credentials missing")
        elif not [k for k in self.creds.kinds() if k not in self._rejected]:
            self._set_state("unavailable", "credentials rejected")
        elif not self._library():
            self._set_state("unavailable", "library missing")
        else:
            self.accepting = True
            if self.state in ("off", "paused", "unavailable"):
                self._set_state("connecting", "")
            return
        self.accepting = False
        self._drain()

    def _library(self) -> bool:
        if self._available is None:
            self._available = bool(self.api.available())
        return self._available

    def _drain(self) -> None:
        """Forget the partial chunk; audio still queued is skipped as it comes up."""
        self._chunk, self._chunk_n, self._chunk_t, self._next_t = [], 0, None, None

    # ------------------------------------------------------------------ audio
    def _audio(self, t: float, samples: Any) -> None:
        if not self.accepting:
            return
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if t < self.muted_until:
            x = np.zeros_like(x)  # our own reply never goes to the cloud
        if self._next_t is not None:
            gap = t - self._next_t
            if gap > 2.0 or gap < -0.05:
                # a long capture gap, or time went backwards (a replay restarted): the stream's
                # clock would be wrong, so the next audio starts a new stream
                self._flush_chunk()
                self._end_streams(cancel=False, keep_overlap=False)
                self._chunk, self._chunk_n, self._chunk_t = [], 0, None
            elif gap > 1.5 / RATE:
                self._add(np.zeros(round(gap * RATE), np.float32), self._next_t)
        self._add(x, t)
        self._next_t = t + len(x) / RATE
        self._last_audio = self.clock()

    def _add(self, x: np.ndarray, t: float) -> None:
        if self._chunk_t is None:
            self._chunk_t = t
        self._chunk.append(x)
        self._chunk_n += len(x)
        need = round(self.s.chunk_ms * RATE / 1000)
        while self._chunk_n >= need:
            joined = np.concatenate(self._chunk)
            head, rest = joined[:need], joined[need:]
            self._send(head, self._chunk_t)
            self._chunk_t += need / RATE
            self._chunk = [rest] if len(rest) else []
            self._chunk_n = len(rest)

    def _flush_chunk(self) -> None:
        if self._chunk_n and self._chunk_t is not None:
            self._send(np.concatenate(self._chunk), self._chunk_t)
        self._chunk, self._chunk_n, self._chunk_t = [], 0, None

    def _send(self, x: np.ndarray, t: float) -> None:
        pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        self._overlap.append((t, pcm))
        while self._overlap and t - self._overlap[0][0] > self.s.overlap_s:
            self._overlap.popleft()
        now = self.clock()
        if self.active is None:
            if now < self._retry_at:
                return  # waiting to reconnect: local captions only meanwhile
            self._open(t, [])
            if self.active is None:
                return
        if not self.active.feed(pcm):
            # Google isn't taking audio as fast as it comes: network trouble
            logger.warning("cloud: the stream fell behind; restarting it")
            self._fail("network")
            return
        self.sent_s += len(pcm) / 2 / RATE

    # ------------------------------------------------------------------ streams
    def _open(self, t0: float, replay: list[tuple[float, bytes]]) -> None:
        kinds = [k for k in self.creds.kinds() if k not in self._rejected]
        if not kinds:
            return
        kind = kinds[0]
        try:
            client = self.api.client(self.creds, kind, self.s)
            config = self.api.streaming_config(self.s, self.language)
        except Exception as exc:  # noqa: BLE001 - a bad key file, an old library
            logger.warning("cloud: could not create the Google client (%s)", type(exc).__name__)
            self._rejected.add(kind)
            return
        self._n += 1
        stream = _Stream(
            f"cloud-{self._n}",
            replay[0][0] if replay else t0,
            round(self.s.queue_s * 1000 / max(self.s.chunk_ms, 10)),
            kind,
            self.language,
        )
        stream.opened = self.clock()
        for _, pcm in replay:
            stream.feed(pcm)
        stream.thread = threading.Thread(
            target=self._read, args=(stream, client, config), name=stream.id, daemon=True
        )
        self.active = stream
        stream.thread.start()
        self._speech_since = None
        if self.state not in ("on", "fallback"):
            self._set_state("connecting", "")

    def _requests(self, stream: _Stream) -> Iterator[Any]:
        for chunk in stream.chunks():
            yield self.api.request(chunk)

    def _read(self, stream: _Stream, client: Any, config: Any) -> None:
        try:
            responses = self.api.recognize(client, config, self._requests(stream))
            stream.call = responses
            for resp in responses:
                if stream.cancelled or self.closed.is_set():
                    break
                self._on_response(stream, resp)
        except Exception as exc:  # noqa: BLE001 - reported to the pump
            if not stream.cancelled:
                stream.error = exc
        finally:
            stream.closing.set()
            self.inbox.put(("stream_done", {"stream": stream}))

    def _on_response(self, stream: _Stream, resp: Any) -> None:
        """Runs on the stream's thread: publish the new or re-tagged words."""
        now = self.clock()
        err = getattr(resp, "error", None)
        if err is not None and int(getattr(err, "code", 0) or 0) != 0:
            raise _StreamError(int(err.code))
        stream.responses += 1
        with self._lock:
            self._last_response = now
            self._speech_since = None
        for res in parse_response(resp):
            t_end = stream.t0 + res.end
            latency = now - t_end
            with self._lock:
                self.latencies.append(latency)
            words = []
            for w, a, b, tag in res.words:
                if not tag:
                    continue
                key = round(a * 100)
                if stream.words.get(key) == (w, tag):
                    continue
                stream.words[key] = (w, tag)
                words.append((w, round(stream.t0 + a, 3), round(stream.t0 + b, 3), tag))
            if not words and not res.final:
                continue
            self.bus.publish(
                SPEAKER_CLOUD,
                {
                    "stream_id": stream.id,
                    "words": words,
                    "final": res.final,
                    "t_end": round(t_end, 3),
                    "latency_s": round(latency, 3),
                    "lang": res.lang or stream.language,
                    "confidence": res.confidence,
                },
            )

    def _end_streams(self, cancel: bool, keep_overlap: bool = True) -> None:
        """Stop feeding the active stream (half-close, or cancel when nothing more is wanted)."""
        s = self.active
        self.active = None
        if s is not None:
            if cancel:
                s.cancel()
            else:
                s.close()
                self.old.append(s)
        if not keep_overlap or cancel:
            self._overlap.clear()

    def _restart(self) -> None:
        """A seamless new stream: it hears the last overlap_s of audio again first."""
        old = self.active
        if old is None:
            return
        replay = list(self._overlap)
        self.active = None
        old.close()
        self.old.append(old)
        self.restarts += 1
        self._open(replay[-1][0] if replay else self.clock(), replay)
        if self.active is not None and replay:
            self.sent_s += sum(len(p) for _, p in replay) / 2 / RATE

    def _fail(self, reason: str, delay: float | None = None) -> None:
        """Fall back to local captions and reconnect later."""
        self._end_streams(cancel=True)
        if delay is None:
            steps = list(self.s.retry_s) or [5.0]
            delay = float(steps[min(self._retries, len(steps) - 1)])
            self._retries += 1
        self._retry_at = self.clock() + delay
        with self._lock:
            self.latencies.clear()  # the next stream is judged on its own results
            self._speech_since = None
        self._set_state("fallback", reason)

    def _stream_done(self, stream: _Stream) -> None:
        if stream in self.old:
            self.old.remove(stream)
        if stream is not self.active:
            return  # an old stream finished after its restart: nothing to do
        exc = stream.error
        self.active = None
        if exc is None:
            return  # Google ended it (a quiet spell): the next audio opens a new one
        kind = _StreamError.kind_of(exc) if isinstance(exc, _StreamError) else error_kind(exc)
        logger.warning("cloud: stream ended with %s (%s)", type(exc).__name__, kind)
        if kind == "auth":
            self._rejected.add(stream.kind)
            if [k for k in self.creds.kinds() if k not in self._rejected]:
                self._retry_at = -1e9  # try the next way to sign in at once
                return
            self._set_state("unavailable", "credentials rejected")
            self.accepting = False
            self._drain()
        elif kind == "quota":
            self._fail("quota", self.s.quota_retry_s)
        elif kind == "limit":
            self._retry_at = -1e9  # a stream ran too long: just start the next one
        else:
            self._fail("error" if kind == "error" else "network")

    # ------------------------------------------------------------------ health
    def _maintain(self, now: float) -> None:
        s = self.active
        if s is None:
            if self.state == "fallback" and now >= self._retry_at and self.accepting:
                self._set_state("connecting", "")
            return
        if now - self._last_audio > self.s.idle_close_s:
            self._end_streams(cancel=False)  # no audio coming in: Google would time out
            return
        if (s.samples / RATE) >= self.s.stream_max_s:
            self._restart()
            return
        with self._lock:
            lat = list(self.latencies)
            last, since = self._last_response, self._speech_since
        slow = len(lat) >= min(3, self.latencies.maxlen or 3) and (
            statistics.median(lat) * 1000 > self.s.latency_fallback_ms
        )
        stalled = since is not None and now - since > self.s.stall_s and last < since
        if stalled and now - since > 3 * self.s.stall_s:
            logger.warning("cloud: no results for %.0f s of speech; reconnecting", now - since)
            with self._lock:
                self._speech_since = None
            self._fail("slow")
            return
        if slow or stalled:
            self._set_state("fallback", "slow")
        elif s.responses or now - s.opened >= self.s.connect_grace_s:
            self._retries = 0
            self._set_state("on", "")

    # ------------------------------------------------------------------ reports
    def latency_ms(self) -> int | None:
        with self._lock:
            lat = list(self.latencies)
        return round(statistics.median(lat) * 1000) if lat else None

    def _set_state(self, state: str, reason: str) -> None:
        if (state, reason) != (self.state, self.reason):
            self.state, self.reason = state, reason
            if state != "on":
                logger.info("cloud: %s%s", state, f" ({reason})" if reason else "")
        self._publish_state()

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "state": self.state,
            "reason": self.reason,
            "latency_ms": self.latency_ms() if self.state in ("on", "fallback") else None,
            "provider": self.s.provider,
            "model": self.s.model_for(self.language),
            "language": self.language,
            "languages": self.s.languages,
            "credentials": self.creds.present,
        }

    def _publish_state(self, force: bool = False) -> None:
        snap = self.snapshot()
        key = tuple(v for k, v in snap.items() if k not in ("latency_ms", "languages"))
        now = self.clock()
        t, last = self._published_latency
        lat = snap["latency_ms"]
        moved = lat is not None and (last is None or abs(lat - last) >= 100) and now - t >= 2.0
        if force or key != self._published or moved:
            self._published = key
            self._published_latency = (now, lat)
            self.bus.publish(CLOUD_STATE, snap)

    def _report(self, now: float) -> None:
        self._publish_state()
        if now - self._status_t < 1.0:
            return
        self._status_t = now
        self.bus.publish(
            "status.part",
            {
                "part": "cloud",
                "ok": self.state in ("off", "connecting", "on", "paused"),
                "detail": self.reason or self.state,
                "metrics": {
                    "state": self.state,
                    "latency_ms": self.latency_ms(),
                    "reason": self.reason,
                    "restarts": self.restarts,
                    "sent_s": round(self.sent_s, 1),
                    "dropped": self.dropped,
                },
            },
        )

    # ------------------------------------------------------------------ the wearer's choice
    def _load_choice(self) -> None:
        if not self.s.remember:
            return
        try:
            data = json.loads(self.choice_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data.get("enabled"), bool):
            self.enabled = data["enabled"]
        if data.get("language") in self.s.models:
            self.language = data["language"]

    def _save_choice(self) -> None:
        if not self.s.remember:
            return
        body = json.dumps({"enabled": self.enabled, "language": self.language})
        try:
            self.choice_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.choice_path.with_suffix(".json.tmp")
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, self.choice_path)
        except OSError as exc:
            logger.warning("cloud: could not keep the setting (%s)", type(exc).__name__)


class _StreamError(Exception):
    """An error status inside a response (rare; gRPC errors usually raise instead)."""

    def __init__(self, code: int):
        super().__init__(f"stream error {code}")
        self.code = code

    @staticmethod
    def kind_of(exc: _StreamError) -> str:
        return _GRPC_KIND.get(exc.code, "network")
