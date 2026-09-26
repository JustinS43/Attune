"""Audio service: capture, VAD, ASR, mute gating and consented voice prints.

Voice enrollment (after a consented face result) reports `enroll.progress` {part: voice,
fraction = seconds of their speech collected / voice.enroll_s} as their captions arrive
(TODO P-28), and ends with a failed `enroll.result` when their face leaves the view ("stay in
view") or `voice.enroll_timeout_s` passes without enough speech ("not enough speech").
"""

from __future__ import annotations

import logging
import math
from collections import deque
from pathlib import Path
from uuid import uuid4

import numpy as np

from .asr import NemotronASR, Recognition, UtteranceLevel
from .asr_whisper import WhisperASR
from .language_id import LanguageID
from .mic import AudioRing, MicReader
from .runtime import Worker, engine_clock
from .vad import Segmenter, SileroVAD
from .voiceprint import CAMExtractor, VoicePrints

logger = logging.getLogger(__name__)


class AudioService:
    """Publish documented events; injected adapters support device-free replay tests."""

    def __init__(self, bus, config, *, vad=None, asr=None, voices=None, language=None, mic=None):
        self.bus, self.config = bus, config
        self.vad, self.asr, self.voices, self.language, self.mic = vad, asr, voices, language, mic
        self.worker = Worker(bus, "audio", self._handle)
        self.worker.cleanup = self._cleanup
        self.segmenter = Segmenter(config["audio"])
        self.ring = AudioRing()
        self.speech_intervals: deque[tuple[float, float]] = deque()
        self.paused = False
        self.muted_until = float("-inf")
        self.enrollment = None
        self.pending_consent = None
        self.pending = np.empty(0, np.float32)
        self.pending_t = None
        self.utterance: list = []
        # Soft word onsets ("h" in "Hi") score under vad_start, so the frames just
        # before speech is detected are kept and prepended to the utterance.
        self.pre_roll: deque[np.ndarray] = deque(
            maxlen=max(0, round(config["audio"].get("pre_roll_ms", 320) / 32))
        )
        self.utt_t0 = 0.0
        self.speech_audio: list[np.ndarray] = []
        self.sent = 0
        self.level = UtteranceLevel(config["audio"]["target_rms"])
        self.normalized: list[np.ndarray] = []
        self.utt_id = ""
        self.languages = list(config["audio"]["languages"])

    def start(self) -> None:
        """Load local models before enabling capture; start no downloads."""
        self.clock = engine_clock(self.config)
        cfg = self.config["audio"]
        self.vad = self.vad or SileroVAD()
        self.language = self.language or LanguageID(cfg["languages"])
        if self.asr is None:
            self.asr = self._make_asr(self.languages)
        if self.voices is None:
            voice = self.config["voice"]
            self.voices = VoicePrints(
                Path(self.config["engine"]["data_dir"]) / "people",
                CAMExtractor(voice["model_path"], voice["provider"]),
                self.config["fusion"]["voice_match"],
                voice["enroll_s"],
                voice["match_s"],
            )
        for topic in (
            "audio.block",
            "speech_out.playing",
            "voice.harvest",
            "enroll.result",
            "command",
            "person.changed",
            "session.forget",
            "paused",
            "vision.track_lost",
            "caption",
        ):
            self.worker.subscribe(topic)
        self.worker.start()
        if self.mic is not False:
            self.mic = self.mic or MicReader(
                cfg, self.clock, lambda block: self.worker.publish("audio.block", block)
            )
            self.worker.health = self.mic.health
            try:
                self.mic.start()
            except Exception:
                self.stop()
                raise

    def stop(self) -> None:
        """Stop capture and invalidate pending inference before clearing session data."""
        self.worker.closed.set()
        if self.mic:
            self.mic.stop()
        self.worker.stop()
        if not self.worker.thread:
            self._cleanup()

    def _cleanup(self) -> None:
        self._reset()
        if self.voices:
            self.voices.forget()
        self.ring.clear()
        self.speech_intervals.clear()
        self.enrollment = self.pending_consent = None

    def _reset(self) -> None:
        self.pending = np.empty(0, np.float32)
        self.pending_t = None
        self.utterance.clear()
        self.pre_roll.clear()
        self.speech_audio.clear()
        self.sent = 0
        self.level.reset()
        self.normalized.clear()
        self.segmenter.reset()
        if self.asr:
            self.asr.reset()
        if self.vad and hasattr(self.vad, "reset"):
            self.vad.reset()

    def _make_asr(self, languages: list[str]):
        """Prefer local multilingual Nemotron, then recover with local Whisper."""
        try:
            return NemotronASR(self.config["nemotron"] | {"languages": languages})
        except (ImportError, FileNotFoundError, RuntimeError):
            logger.warning("Nemotron unavailable; selecting local Whisper")
            return WhisperASR(self.config["whisper"] | {"languages": languages})

    def _speech_span(self, start: float, end: float) -> np.ndarray:
        """Return only VAD-positive samples in an available attributed span."""
        audio = self.ring.span(start, end)
        parts = [
            audio[round((max(start, a) - start) * 16000) : round((min(end, b) - start) * 16000)]
            for a, b in self.speech_intervals
            if a < end and b > start
        ]
        return np.concatenate(parts) if parts else np.empty(0, np.float32)

    def _handle(self, topic: str, e: dict, generation: int) -> None:
        if topic == "session.forget":
            self._reset()
            self.ring.clear()
            self.speech_intervals.clear()
            self.voices.forget()
            self.enrollment = self.pending_consent = None
        elif topic == "paused":
            self.paused = e["paused"]
            self._reset()
            self.ring.clear()
            self.speech_intervals.clear()
            self.enrollment = self.pending_consent = None
        elif topic == "speech_out.playing":
            self.muted_until = (
                float("inf")
                if e["state"] == "start"
                else e["t"] + self.config["audio"]["mute_after_reply_s"]
            )
            self._reset()
            self.ring.clear()
            self.speech_intervals.clear()
        elif topic == "person.changed" and e["action"] == "deleted":
            self.voices.delete(e["person_id"])
            self.enrollment = self.pending_consent = None
        elif topic == "voice.harvest" and not self.paused and self.clock() >= self.muted_until:
            audio = self._speech_span(e["t0"], e["t1"])
            if generation == self.worker.generation:
                self.voices.harvest(e["person_id"], audio)
        elif topic == "command":
            name, args = e["name"], e.get("args", {})
            if name == "enroll.start":
                self.enrollment = self.pending_consent = None
                consent_t = args.get("consent_t")
                if (
                    args.get("consent") is True
                    and isinstance(consent_t, (int, float))
                    and not isinstance(consent_t, bool)
                    and math.isfinite(consent_t)
                    and "track_id" in args
                ):
                    self.pending_consent = dict(args)
            elif name == "person.delete":
                self.voices.delete(args["person_id"])
                self.enrollment = self.pending_consent = None
            elif name == "languages.set":
                langs = args["langs"]
                language = LanguageID(langs)
                langs = language.languages
                asr = self._make_asr(langs)
                # Model creation can fail or be invalidated while loading. Commit
                # the recognizer and language detector together only on success.
                if generation != self.worker.generation or self.worker.closed.is_set():
                    return
                self._reset()
                self.language, self.asr, self.languages = language, asr, langs
        elif topic == "enroll.result" and e["part"] == "face":
            if (
                self.pending_consent
                and e["ok"]
                and e.get("track_id") == self.pending_consent["track_id"]
            ):
                self.enrollment = self.pending_consent | {
                    "person_id": e["person_id"],
                    "audio": [],
                    "seen": set(),
                    "started_t": self.clock(),
                }
                self.pending_consent = None
                self._voice_progress(0.0, "keep talking", generation)
        elif topic == "vision.track_lost":
            if self.enrollment and self.enrollment["track_id"] == e["track_id"]:
                self._voice_failed("stay in view", generation)
            if self.pending_consent and self.pending_consent["track_id"] == e["track_id"]:
                self.pending_consent = None
        elif topic == "caption" and e.get("final") and self.enrollment:
            speaker = e["speaker"]
            if (
                speaker.get("kind") == "face"
                and speaker.get("track_id") == self.enrollment["track_id"]
                and e.get("words")
                and e["utt_id"] not in self.enrollment["seen"]
            ):
                a, b = e["words"][0][1], e["words"][-1][2]
                if a < self.enrollment["started_t"]:
                    return
                self.enrollment["seen"].add(e["utt_id"])
                audio = self._speech_span(a, b)
                self.enrollment["audio"].append(audio)
                combined = np.concatenate(self.enrollment["audio"])
                need = self.config["voice"]["enroll_s"] * 16000
                self._voice_progress(min(1.0, len(combined) / need), "", generation)
                if len(combined) >= need:
                    request = self.enrollment
                    self.voices.enroll(
                        request["person_id"],
                        combined,
                        True,
                        request["consent_t"],
                        guard=lambda: (
                            generation == self.worker.generation and not self.worker.closed.is_set()
                        ),
                        lock=self.worker._lock,
                    )
                    self.worker.publish(
                        "enroll.result",
                        {
                            "person_id": request["person_id"],
                            "part": "voice",
                            "ok": True,
                            "reason": "",
                            "track_id": request["track_id"],
                        },
                        generation,
                    )
                    self.enrollment = None
        elif topic == "audio.block" and e["sample_rate"] == 16000:
            timeout = float(self.config["voice"].get("enroll_timeout_s", 45.0))
            if self.enrollment and self.clock() - self.enrollment["started_t"] > timeout:
                self._voice_failed("not enough speech", generation)
            self._audio(e, generation)

    def _voice_progress(self, fraction: float, hint: str, generation: int) -> None:
        request = self.enrollment
        self.worker.publish(
            "enroll.progress",
            {
                "track_id": request["track_id"],
                "person_id": request["person_id"],
                "part": "voice",
                "fraction": round(fraction, 3),
                "hint": hint,
            },
            generation,
        )

    def _voice_failed(self, reason: str, generation: int) -> None:
        """End a running voice enrollment without saving anything, and say why."""
        request, self.enrollment = self.enrollment, None
        self.worker.publish(
            "enroll.result",
            {
                "person_id": request["person_id"],
                "part": "voice",
                "ok": False,
                "reason": reason,
                "track_id": request["track_id"],
            },
            generation,
        )

    def _audio(self, e: dict, generation: int) -> None:
        samples = np.asarray(e["samples"], dtype=np.float32)
        if self.paused or e["t"] < self.muted_until:
            self._reset()
            return
        self.ring.append(e["t"], samples)
        if self.pending_t is None:
            self.pending_t = e["t"]
        expected = self.pending_t + len(self.pending) / 16000
        if abs(expected - e["t"]) > 1.5 / 16000:
            self._reset()
            self.pending_t = e["t"]
        self.pending = np.concatenate((self.pending, samples))
        while len(self.pending) >= 512:
            frame, self.pending = self.pending[:512], self.pending[512:]
            t = self.pending_t
            self.pending_t += 0.032
            prob = self.vad(frame)
            active, _began, ended = self.segmenter.feed(t, prob)
            if active:
                self.speech_audio.append(frame.copy())
                if self.speech_intervals and abs(self.speech_intervals[-1][1] - t) < 1 / 16000:
                    self.speech_intervals[-1] = (self.speech_intervals[-1][0], t + 0.032)
                else:
                    self.speech_intervals.append((t, t + 0.032))
            while self.speech_intervals and self.speech_intervals[0][1] <= t - self.ring.seconds:
                self.speech_intervals.popleft()
            self.worker.publish(
                "audio.vad", {"t": t, "is_speech": active, "prob": prob}, generation
            )
            if self.segmenter.start is not None:
                if not self.utterance:
                    self.utt_id = str(uuid4())
                    self.utterance.extend(self.pre_roll)
                    self.utt_t0 = self.segmenter.start - 0.032 * len(self.pre_roll)
                    self.pre_roll.clear()
                self.utterance.append(frame.copy())
            else:
                self.pre_roll.append(frame.copy())
            count = len(self.utterance) * 512
            final = ended or count >= self.config["audio"]["max_utterance_s"] * 16000
            if self.segmenter.confirmed and (
                final or count - self.sent >= self.config["audio"]["asr_chunk_ms"] * 16
            ):
                audio = np.concatenate(self.utterance)
                result = self._recognize(audio[self.sent :], final)
                self.sent = count
                if generation != self.worker.generation:
                    return
                start = self.utt_t0
                if result.text:
                    lang = self.language.detect(result.text, result.lang) if final else result.lang
                    self.worker.publish(
                        "audio.transcript",
                        {
                            "utt_id": self.utt_id,
                            "t_start": start,
                            "t_end": t + 0.032,
                            "text": result.text,
                            "final": final,
                            "lang": lang,
                            "words": [(w, start + a, start + b) for w, a, b in result.words],
                        },
                        generation,
                    )
                speech = np.concatenate(self.speech_audio) if self.speech_audio else np.empty(0)
                person, score = self.voices.match(speech)
                self.worker.publish(
                    "audio.voice_match",
                    {"utt_id": self.utt_id, "person_id": person, "score": score},
                    generation,
                )
            if final:
                self.utterance.clear()
                self.speech_audio.clear()
                self.normalized.clear()
                self.level.reset()
                self.sent = 0
                self.segmenter.reset()
                self.asr.reset()

    def _recognize(self, samples: np.ndarray, final: bool) -> Recognition:
        normalized = self.level.feed(samples)
        self.normalized.append(normalized)
        try:
            return self.asr.feed(normalized, final)
        except RuntimeError:
            if not isinstance(self.asr, NemotronASR):
                self._reset()
                raise
            logger.warning("Nemotron decoding failed; retrying utterance with local Whisper")
            try:
                fallback = WhisperASR(
                    self.config["whisper"] | {"languages": self.languages}
                )
                result = fallback.feed(np.concatenate(self.normalized), final)
            except Exception:
                self._reset()
                raise
            self.asr = fallback
            return result
