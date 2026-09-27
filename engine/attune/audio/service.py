"""Audio service: capture, VAD, ASR, mute gating and consented voice prints.

Voice enrollment (after a consented face result) reports `enroll.progress` {part: voice,
fraction = seconds of their speech collected / voice.enroll_s} as their captions arrive
(TODO P-29), and ends with a failed `enroll.result` when their face leaves the view ("stay in
view") or `voice.enroll_timeout_s` passes without enough speech ("not enough speech").
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import numpy as np

from .asr import NemotronASR, Recognition, UtteranceLevel
from .asr_whisper import WhisperASR
from .language_id import LanguageID
from .mic import AudioRing, MicReader
from .runtime import Worker, engine_clock
from .vad import InputGain, Segmenter, SileroVAD
from .voiceprint import CAMExtractor, VoicePrints

logger = logging.getLogger(__name__)


def _is_16k(event) -> bool:
    rate = event.get("sample_rate") if isinstance(event, dict) else None
    return (rate if rate is not None else getattr(event, "sample_rate", None)) == 16000


class AudioService:
    """Publish documented events; injected adapters support device-free replay tests."""

    def __init__(self, bus, config, *, vad=None, asr=None, voices=None, language=None, mic=None):
        self.bus, self.config = bus, config
        self.vad, self.asr, self.voices, self.language, self.mic = vad, asr, voices, language, mic
        cfg = config["audio"]
        # The inbox holds 16 kHz blocks only (100 a second): room for inbox_s of audio, so a
        # slow step (a final, a voice match) never drops audio and never cuts an utterance.
        self.worker = Worker(
            bus, "audio", self._handle, maxsize=round(100 * cfg.get("inbox_s", 30.0))
        )
        self.auto_voice_tracks: dict[str, str] = {}
        self.worker.cleanup = self._cleanup
        self.segmenter = Segmenter(cfg)
        # gain before the VAD only: quiet or distant speech must be detected at all (A-24).
        # On unless `vad_gain = false` (A-31): without it Silero missed most of a talker
        # 2-4 m from the laptop mic (vad.InputGain has the numbers).
        self.vad_gain = InputGain(cfg) if cfg.get("vad_gain", True) else None
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
        # before speech is detected are kept and prepended to the utterance. The
        # recogniser also drops a first word with less than ~0.16 s of audio before it,
        # so a short pre-roll is padded with silence (see _recognize).
        self.pre_roll: deque[np.ndarray] = deque(
            maxlen=max(0, round(cfg.get("pre_roll_ms", 320) / 32))
        )
        self.utt_t0 = 0.0
        self.lead = 0  # samples the recogniser heard before this utterance (its word times)
        self.pad = 0  # silence put before this utterance's audio for the recogniser
        self.continued = False  # this utterance carries on the last one's recogniser stream
        self.speech_audio: list[np.ndarray] = []
        self.sent = 0
        self.level = UtteranceLevel(
            cfg["target_rms"],
            window_s=cfg.get("level_window_s", 0.5),
            rise_db_s=cfg.get("level_rise_db_s", 0.0),
            percentile=cfg.get("level_percentile", 50.0),
        )
        self.normalized: list[np.ndarray] = []
        self.utt_id = ""
        self.shown: Recognition | None = None  # the last draft published for this utterance
        self.voice_at = 0  # speech samples at this utterance's last voice match
        self.word_t: float | None = None  # when this utterance's words last changed
        self.languages = list(cfg["languages"])
        self._asr_lock = threading.Lock()  # the recogniser is shared with rescues
        self._rescues = ThreadPoolExecutor(1, thread_name_prefix="audio-rescue")

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
                options=voice,  # A-21: station threshold and the glasses-mic refinement
            )
        # The 32 kHz stream is for the sound alerts: keep it out of this inbox.
        self.worker.subscribe("audio.block", accept=_is_16k)
        for topic in (
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
                cfg,
                self.clock,
                lambda block: self.worker.publish("audio.block", block),
                # Only return to the preferred mic between utterances.
                busy=lambda: self.segmenter.start is not None,
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
        self._rescues.shutdown(wait=False, cancel_futures=True)
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
        self._end_utterance()
        self.pre_roll.clear()
        if self.vad and hasattr(self.vad, "reset"):
            self.vad.reset()

    def _end_utterance(self, keep_tail: int = 0, keep_stream: bool = False) -> None:
        """Forget the current utterance. `keep_tail`: this many of its last frames (the
        silence that ended it) become the pre-roll of the next one, so speech that starts
        again right away still has audio before its first word. `keep_stream`: the next
        utterance carries on the recogniser's stream (a split in the middle of talk)."""
        keep = min(keep_tail, self.pre_roll.maxlen or 0)
        tail = self.utterance[-keep:] if keep else []
        self.utterance.clear()
        self.speech_audio.clear()
        self.sent = 0
        self.shown = None
        self.voice_at = 0
        self.word_t = None
        self.normalized.clear()
        self.segmenter.reset()
        self.continued = keep_stream
        self.pad = 0
        if keep_stream:
            self.lead = getattr(self.asr, "samples", 0)  # its word times go on from there
        else:
            self.lead = 0
            self.level.reset()
            if self.asr:
                with self._asr_lock:
                    self.asr.reset()
        self.pre_roll.clear()
        self.pre_roll.extend(tail)

    def _make_asr(self, languages: list[str]):
        """Prefer local multilingual Nemotron, then recover with local Whisper."""
        try:
            return NemotronASR(self.config["nemotron"] | {"languages": languages})
        except (ImportError, FileNotFoundError, RuntimeError):
            logger.warning("Nemotron unavailable; selecting local Whisper")
            return WhisperASR(self.config["whisper"] | {"languages": languages})

    def _speech_span(self, start: float, end: float) -> np.ndarray:
        """Return only VAD-positive samples in an available attributed span.

        A span the ring can't give (a gap, or already dropped) is empty: one missed voice
        sample must not mark the whole audio part as failed.
        """
        try:
            audio = self.ring.span(start, end)
        except ValueError as exc:
            logger.debug("speech span %.2f-%.2f unavailable: %s", start, end, exc)
            return np.empty(0, np.float32)
        parts = [
            audio[round((max(start, a) - start) * 16000) : round((min(end, b) - start) * 16000)]
            for a, b in self.speech_intervals
            if a < end and b > start
        ]
        return np.concatenate(parts) if parts else np.empty(0, np.float32)

    def _handle(self, topic: str, e: dict, generation: int) -> None:
        if topic == "session.forget":
            self.auto_voice_tracks.clear()
            self._reset()
            self.ring.clear()
            self.speech_intervals.clear()
            self.voices.forget()
            self.enrollment = self.pending_consent = None
        elif topic == "paused":
            if e["paused"]:
                self._finish(generation)  # what was said before the pause stays captioned
            self.paused = e["paused"]
            self._reset()
            self.ring.clear()
            self.speech_intervals.clear()
            self.enrollment = self.pending_consent = None
        elif topic == "speech_out.playing":
            if e["state"] == "start":
                self._finish(generation)  # someone was talking when the reply started
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
                key = e["person_id"]
                result = self.voices.harvest(key, audio, e.get("talkers", 1))
                target = self.auto_voice_tracks.get(
                    key, key if str(key).startswith("auto-") else None
                )
                if target and result == "session" and self.voices.remember_auto(target, key):
                    self.auto_voice_tracks.pop(key, None)
                    self.worker.publish(
                        "enroll.result",
                        {
                            "person_id": target,
                            "part": "voice",
                            "ok": True,
                            "reason": "",
                            "source": "auto",
                            "track_id": None,
                        },
                        generation,
                    )
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
                self._finish(generation)
                self._reset()
                with self._asr_lock:
                    self.language, self.asr, self.languages = language, asr, langs
        elif topic == "enroll.result" and e["part"] == "face" and e.get("source") == "auto":
            track_id = e.get("track_id")
            person_id = e.get("person_id")
            if e.get("ok") and track_id is not None and person_id:
                key = f"track-{track_id}"
                self.auto_voice_tracks[key] = person_id
                if self.voices.remember_auto(person_id, key):
                    self.auto_voice_tracks.pop(key, None)
                    self.worker.publish(
                        "enroll.result",
                        {
                            "person_id": person_id,
                            "part": "voice",
                            "ok": True,
                            "reason": "",
                            "source": "auto",
                            "track_id": None,
                        },
                        generation,
                    )
        elif topic == "enroll.result" and e["part"] == "voice" and e.get("source") == "station":
            # A-21: the enrollment station saved this print from the laptop mic; load it
            if e.get("ok") and e.get("person_id"):
                self.voices.load(e["person_id"])
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
        if self.paused:
            self._reset()
            return
        muted = e["t"] < self.muted_until
        if not muted:
            self.ring.append(e["t"], samples)
        if self.pending_t is None:
            self.pending_t = e["t"]
        expected = self.pending_t + len(self.pending) / 16000
        if abs(expected - e["t"]) > 1.5 / 16000:
            # a capture gap: caption what was heard up to it instead of dropping it
            self._finish(generation)
            self._reset()
            self.pending_t = e["t"]
        self.pending = np.concatenate((self.pending, samples))
        while len(self.pending) >= 512:
            frame, self.pending = self.pending[:512], self.pending[512:]
            t = self.pending_t
            self.pending_t += 0.032
            if muted:
                # Our own reply is playing: nothing is recognised, but the newest frames
                # stay ready as the lead-in of speech still going when the mute ends.
                self.pre_roll.append(frame.copy())
                continue
            self._frame(frame, t, generation)

    def _frame(self, frame: np.ndarray, t: float, generation: int) -> None:
        cfg = self.config["audio"]
        prob = self.vad(self.vad_gain(frame) if self.vad_gain else frame)
        active, _began, ended = self.segmenter.feed(t, prob)
        if active:
            self.speech_audio.append(frame.copy())
            if self.speech_intervals and abs(self.speech_intervals[-1][1] - t) < 1 / 16000:
                self.speech_intervals[-1] = (self.speech_intervals[-1][0], t + 0.032)
            else:
                self.speech_intervals.append((t, t + 0.032))
        while self.speech_intervals and self.speech_intervals[0][1] <= t - self.ring.seconds:
            self.speech_intervals.popleft()
        self.worker.publish("audio.vad", {"t": t, "is_speech": active, "prob": prob}, generation)
        if self.segmenter.start is not None:
            if not self.utterance:
                self.utt_id = str(uuid4())
                self.utterance.extend(self.pre_roll)
                self.utt_t0 = t - 0.032 * len(self.pre_roll)
                if not self.continued:
                    # too little audio before the first word: the recogniser gets silence first
                    self.pad = self.lead = (self.pre_roll.maxlen - len(self.pre_roll)) * 512
                self.pre_roll.clear()
            self.utterance.append(frame.copy())
        else:
            self.pre_roll.append(frame.copy())
        count = len(self.utterance) * 512
        # A long utterance ends at its first pause after soft_split_s (a sentence gap),
        # and at max_utterance_s whatever happens, so finals, translations and history
        # never wait for a whole monologue. A pause is soft_split_gap_s of no speech: the
        # VAD also dips for a frame or two inside words ("trees"), and a split there cuts
        # the word. Background talk can keep the VAD on for good; then a pause is also
        # soft_split_word_gap_s with no new word from the recogniser (A-24).
        soft = cfg.get("soft_split_s")
        quiet = self.segmenter.silence_s
        word_gap = cfg.get("soft_split_word_gap_s")
        last_word = self.word_t if self.word_t is not None else self.utt_t0
        split = (
            bool(soft)
            and count >= soft * 16000
            and (
                (not active and quiet >= cfg.get("soft_split_gap_s", 0.1) - 1e-6)
                or bool(word_gap and t + 0.032 - last_word >= word_gap)
            )
        )
        final = ended or split or count >= cfg["max_utterance_s"] * 16000
        # A split in the middle of talk cuts the recogniser's stream instead of starting a
        # new one: a fresh stream drops the first words of quiet speech (A-22).
        cut = (
            final
            and not ended
            and isinstance(self.asr, NemotronASR)
            and getattr(self.asr, "samples", 0) < cfg.get("split_context_s", 120) * 16000
        )
        # Nemotron consumes each VAD frame cheaply; Whisper re-decodes the growing
        # utterance on every call, so a frame-sized interval makes it fall behind.
        draft_ms = (
            self.config["whisper"].get("draft_interval_ms", cfg["asr_chunk_ms"])
            if isinstance(self.asr, WhisperASR)
            else cfg["asr_chunk_ms"]
        )
        if self.segmenter.confirmed and (final or count - self.sent >= draft_ms * 16):
            fresh = self.utterance[self.sent // 512 :]
            audio = np.concatenate(fresh) if fresh else np.empty(0, np.float32)
            result = self._recognize(audio, final and not cut)
            if cut:
                with self._asr_lock:
                    result = self.asr.cut()
            self.sent = count
            if generation != self.worker.generation:
                return
            self._publish(result, final, t + 0.032, generation, rescue=not cut)
        if final:
            # the silent frames that ended it become the next utterance's pre-roll (none
            # after a cut: the stream heard them; none after a split at max_utterance_s:
            # those frames are speech already captioned)
            if ended:
                silent = self.pre_roll.maxlen
            else:
                silent = round(quiet / 0.032) if split and not cut else 0
            self._end_utterance(keep_tail=silent, keep_stream=cut)
            if not ended:
                # A split, not an end: the talk goes on. The next utterance starts at once,
                # as speech, so quiet speech the VAD holds only by its hysteresis (between
                # vad_end and vad_start, a distant talker) is not lost after the split.
                self.segmenter.resume(quiet)

    def _finish(self, generation: int) -> None:
        """Caption the utterance in progress now (a pause, a reply starting, a capture gap)."""
        if not self.utterance or not self.segmenter.confirmed or self.asr is None:
            return
        fresh = self.utterance[self.sent // 512 :]
        audio = np.concatenate(fresh) if fresh else np.empty(0, np.float32)
        try:
            result = self._recognize(audio, True)
        except Exception:
            logger.exception("could not finish the utterance in progress")
            return
        self._publish(result, True, self.utt_t0 + len(self.utterance) * 0.032, generation)

    def _publish(
        self, result: Recognition, final: bool, t_end: float, generation: int, rescue: bool = True
    ) -> None:
        """Publish a draft (only when its text changed) or the final, and match the voice."""
        cfg = self.config["audio"]
        if final and not result.text:
            if self.shown is not None:
                # the words were on screen as a draft: keep them
                result = self.shown
            elif rescue:
                self._try_rescue(t_end, generation)
        if result.text and (final or self.shown is None or result.text != self.shown.text):
            self._transcript(self.utt_id, result, final, self.utt_t0, t_end, self.lead, generation)
            if not final:
                self.shown = result
                self.word_t = t_end
        speech = len(self.speech_audio) * 512
        every = cfg.get("voice_match_every_s", 1.0) * 16000
        if final or speech - self.voice_at >= every:
            # The newest voice_match_max_s of speech only: a print of the whole utterance
            # costs ~20 ms per second of audio on every step and stalls long monologues.
            self.voice_at = speech
            window = round(cfg.get("voice_match_max_s", 6.0) * 16000)
            recent: list[np.ndarray] = []
            for block in reversed(self.speech_audio):
                if len(recent) * 512 >= window:
                    break
                recent.append(block)
            audio = np.concatenate(recent[::-1]) if recent else np.empty(0, np.float32)
            person, score = self.voices.match(audio)
            self.worker.publish(
                "audio.voice_match",
                {"utt_id": self.utt_id, "person_id": person, "score": score},
                generation,
            )

    def _transcript(
        self,
        utt_id: str,
        result: Recognition,
        final: bool,
        start: float,
        t_end: float,
        lead: int,
        generation: int,
    ) -> None:
        lang = self.language.detect(result.text, result.lang) if final else result.lang
        shift = lead / 16000
        self.worker.publish(
            "audio.transcript",
            {
                "utt_id": utt_id,
                "t_start": start,
                "t_end": t_end,
                "text": result.text,
                "final": final,
                "lang": lang,
                "words": [
                    (w, start + max(0.0, a - shift), start + max(0.0, b - shift))
                    for w, a, b in result.words
                ],
            },
            generation,
        )

    def _try_rescue(self, t_end: float, generation: int) -> None:
        """A short utterance whose final came out empty: decode it again on the side."""
        seconds = sum(len(a) for a in self.normalized) / 16000
        limit = self.config["audio"].get("rescue_max_s", 2.5)
        if not limit or seconds > limit or not self.normalized:
            return
        if len(self.speech_audio) * 512 < self.config["audio"]["min_speech_ms"] * 16:
            return  # no speech in it (the pause after a split): nothing to hear again
        if not isinstance(self.asr, NemotronASR) or self.continued:
            return  # a continued stream heard it with its context already
        audio = np.concatenate([np.zeros(self.pad, np.float32), *self.normalized])
        job = (self.asr, self.utt_id, audio, self.utt_t0, t_end, self.pad, generation)
        try:
            self._rescues.submit(self._rescue, *job)
        except RuntimeError:  # shutting down
            pass

    def _rescue(self, asr, utt_id, audio, start, t_end, lead, generation) -> None:
        try:
            result = asr.rescue(audio, self._asr_lock)
        except Exception:
            logger.exception("short-utterance rescue failed")
            return
        if result.text and generation == self.worker.generation:
            logger.info("rescued a short utterance the streaming pass missed")
            self._transcript(utt_id, result, True, start, t_end, lead, generation)

    def _recognize(self, samples: np.ndarray, final: bool) -> Recognition:
        normalized = self.level.feed(samples)
        self.normalized.append(normalized)
        if self.pad and not self.sent:
            # first audio of the utterance: silence before it (see self.pad)
            normalized = np.concatenate((np.zeros(self.pad, np.float32), normalized))
        try:
            with self._asr_lock:
                return self.asr.feed(normalized, final)
        except RuntimeError:
            if not isinstance(self.asr, NemotronASR):
                self._reset()
                raise
            logger.warning("Nemotron decoding failed; retrying utterance with local Whisper")
            try:
                fallback = WhisperASR(self.config["whisper"] | {"languages": self.languages})
                result = fallback.feed(np.concatenate(self.normalized), final)
            except Exception:
                self._reset()
                raise
            self.asr = fallback
            # Whisper heard the utterance alone, without the silence or stream before it
            self.lead = self.pad = 0
            self.continued = False
            return result
