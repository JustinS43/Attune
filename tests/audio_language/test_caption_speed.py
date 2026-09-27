"""A-22: words sooner, nothing lost (gaps, pauses, replies, long talk, very short replies)."""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from attune.audio.asr import NemotronASR, Recognition, hold_back
from attune.audio.asr_whisper import WhisperASR
from attune.audio.service import AudioService, _is_16k
from attune.audio.vad import InputGain, Segmenter

SPEECH, QUIET = 0.5, 0.0  # sample values: the fake VAD hears speech above 0.1


def vad(frame):
    return 0.9 if frame.mean() > 0.1 else 0.1


class GrowingASR:
    """A recogniser whose text grows by one word per call with new audio."""

    def __init__(self, final_text=None):
        self.fed, self.words, self.final_text = [], 0, final_text

    def reset(self):
        self.words = 0

    def feed(self, samples, final=False):
        self.fed.append((len(samples), final))
        if len(samples):
            self.words += 1
        if final and self.final_text is not None:
            return Recognition(self.final_text, "en", [])
        text = " ".join(f"w{i}" for i in range(self.words))
        return Recognition(
            text, "en", [(f"w{i}", 0.1 * i, 0.1 * i + 0.1) for i in range(self.words)]
        )


class GrowingWhisper(WhisperASR):
    """Measure fallback pacing without loading model weights."""

    def __init__(self):
        self.fed = []

    def reset(self):
        pass

    def feed(self, samples, final=False):
        self.fed.append((len(samples), final))
        return Recognition("heard", "en", [("heard", 0.0, 0.2)])


class Voices:
    def __init__(self):
        self.calls = []

    def match(self, samples):
        self.calls.append(len(samples))
        return None, 0.0

    def forget(self):
        pass


def service(config, bus, asr, voices=None, **audio):
    cfg = config | {"audio": config["audio"] | {"asr_chunk_ms": 32, "pre_roll_ms": 320} | audio}
    return AudioService(
        bus,
        cfg,
        vad=vad,
        asr=asr,
        voices=voices or Voices(),
        language=SimpleNamespace(detect=lambda text, lang: lang),
        mic=False,
    )


def transcripts(bus, final=None):
    out = [e for t, e in bus.events if t == "audio.transcript"]
    return [e for e in out if final is None or e["final"] == final]


def audio(s, t, seconds, value):
    s._audio({"t": t, "samples": np.full(round(seconds * 16000), value, np.float32)}, 0)


def test_every_frame_is_fed_so_drafts_come_as_soon_as_decoded(config, bus):
    asr = GrowingASR()
    s = service(config, bus, asr)
    audio(s, 10.0, 1.0, SPEECH)
    drafts = transcripts(bus, final=False)
    # confirmed after 250 ms, then one feed per 32 ms frame (not per 560 ms)
    assert len(asr.fed) >= 20
    assert all(n <= 512 for n, _ in asr.fed[1:])
    assert len(drafts) == len({d["text"] for d in drafts})  # only changed text is sent


def test_whisper_fallback_uses_its_own_draft_interval_and_still_finalizes(config, bus):
    cfg = config | {"whisper": {"draft_interval_ms": 560}}
    asr = GrowingWhisper()
    s = service(cfg, bus, asr)
    audio(s, 10.0, 1.2, SPEECH)
    audio(s, 11.2, 0.6, QUIET)
    drafts = [entry for entry in asr.fed if not entry[1]]
    finals = [entry for entry in asr.fed if entry[1]]
    assert 1 <= len(drafts) <= 3  # one decode per 560 ms, not per 32 ms
    assert len(finals) == 1
    assert transcripts(bus, final=True)


def test_a_capture_gap_finishes_the_utterance_instead_of_dropping_it(config, bus):
    s = service(config, bus, GrowingASR())
    audio(s, 10.0, 1.0, SPEECH)
    audio(s, 13.0, 0.5, QUIET)  # 2 s of audio never arrived
    finals = transcripts(bus, final=True)
    assert len(finals) == 1 and finals[0]["text"]
    assert finals[0]["t_start"] == pytest.approx(10.0)  # lead-in silence is not real time


@pytest.mark.parametrize(
    "topic, event",
    [
        ("paused", {"paused": True}),
        ("speech_out.playing", {"state": "start", "t": 11.0}),
        ("command", {"name": "languages.set", "args": {"langs": ["en"]}}),
    ],
)
def test_pausing_replying_or_switching_language_keeps_what_was_said(config, bus, topic, event):
    s = service(config, bus, GrowingASR())
    s._make_asr = lambda languages: GrowingASR()
    audio(s, 10.0, 1.0, SPEECH)
    utt = transcripts(bus)[-1]["utt_id"]
    s._handle(topic, event, 0)
    finals = transcripts(bus, final=True)
    assert [f["utt_id"] for f in finals] == [utt]


def test_an_empty_final_keeps_the_words_already_on_screen(config, bus):
    s = service(config, bus, GrowingASR(final_text=""))
    audio(s, 10.0, 1.0, SPEECH)
    audio(s, 11.0, 0.6, QUIET)
    shown = transcripts(bus, final=False)[-1]["text"]
    finals = transcripts(bus, final=True)
    assert shown and len(finals) == 1 and finals[0]["text"] == shown


class ShortASR(NemotronASR):
    """Streams nothing for a very short reply; the side decode ([audio, gap, audio]) hears it."""

    def __init__(self):
        self.config = {"flush_s": 0.4}
        self.rescued = threading.Event()
        self.rescue_audio = None

    def reset(self):
        pass

    def feed(self, samples, final=False):
        return Recognition("", "en", [])

    def rescue(self, samples, lock, gap_s=0.2):
        with lock:
            self.rescue_audio = samples
        self.rescued.set()
        return Recognition("No", "en", [("No", 0.35, 0.6)])


def test_a_short_reply_heard_as_nothing_is_decoded_again(config, bus):
    asr = ShortASR()
    s = service(config, bus, asr)
    try:
        audio(s, 10.0, 0.4, SPEECH)
        audio(s, 10.4, 0.6, QUIET)
        assert asr.rescued.wait(2)
        deadline = time.monotonic() + 2
        while not transcripts(bus, final=True) and time.monotonic() < deadline:
            time.sleep(0.01)
        final = transcripts(bus, final=True)
        assert len(final) == 1 and final[0]["text"] == "No"
        # the side decode heard silence first (no pre-roll here): the lead-in, then the audio
        assert len(asr.rescue_audio) >= 0.32 * 16000 + 0.4 * 16000
        assert not np.any(asr.rescue_audio[:5000])
        # word times are shifted back by the lead-in
        assert final[0]["words"][0][1] == pytest.approx(final[0]["t_start"] + 0.03, abs=0.01)
    finally:
        s.stop()


def test_a_long_rescue_is_not_attempted(config, bus):
    asr = ShortASR()
    s = service(config, bus, asr, rescue_max_s=0.5)
    try:
        audio(s, 10.0, 1.0, SPEECH)
        audio(s, 11.0, 0.6, QUIET)
        assert not asr.rescued.wait(0.2)
    finally:
        s.stop()


def test_voice_match_runs_about_once_a_second_on_recent_speech(config, bus):
    voices = Voices()
    s = service(config, bus, GrowingASR(), voices=voices, voice_match_max_s=2.0)
    audio(s, 10.0, 4.0, SPEECH)
    audio(s, 14.0, 0.6, QUIET)
    # every 1 s of speech plus once at the end: not once per 32 ms step
    assert 3 <= len(voices.calls) <= 6
    assert max(voices.calls) <= 2.0 * 16000 + 512


def test_long_talk_is_finalised_at_its_first_pause_after_soft_split(config, bus):
    s = service(config, bus, GrowingASR(), soft_split_s=2.0)
    audio(s, 10.0, 2.5, SPEECH)
    audio(s, 12.5, 0.064, QUIET)  # the VAD dips inside a word: not a pause
    audio(s, 12.564, 0.5, SPEECH)
    assert transcripts(bus, final=True) == []
    audio(s, 13.064, 0.15, QUIET)  # a short breath between sentences, far below end_silence
    audio(s, 13.214, 1.0, SPEECH)
    finals = transcripts(bus, final=True)
    assert len(finals) == 1
    audio(s, 14.214, 0.6, QUIET)
    finals = transcripts(bus, final=True)
    assert len(finals) == 2 and finals[0]["utt_id"] != finals[1]["utt_id"]
    # the breath became the second utterance's pre-roll: it starts before the speech
    assert finals[1]["t_start"] < 13.214


def test_speech_right_after_a_final_keeps_its_first_word(config, bus):
    asr = GrowingASR()
    s = service(config, bus, asr)
    audio(s, 10.0, 1.0, SPEECH)
    audio(s, 11.0, 0.45, QUIET)  # ends the first utterance (end_silence 400 ms)
    assert len(transcripts(bus, final=True)) == 1
    asr.fed.clear()
    audio(s, 11.45, 0.5, SPEECH)
    # the second utterance starts with the silence before it (the old tail), padded to 320 ms
    first = asr.fed[0][0]
    assert first >= 0.32 * 16000


def test_only_16k_blocks_take_room_in_the_audio_inbox(config, bus):
    s = service(config, bus, GrowingASR())
    s.worker.subscribe("audio.block", accept=_is_16k)
    bus.publish("audio.block", {"t": 0.0, "sample_rate": 32000, "samples": np.zeros(320)})
    assert s.worker.inbox.qsize() == 0
    bus.publish("audio.block", {"t": 0.0, "sample_rate": 16000, "samples": np.zeros(160)})
    assert s.worker.inbox.qsize() == 1
    s.worker.stop()


def test_a_cut_never_splits_a_word():
    t = ["▁Last", "▁sum", "mer", "▁in", "▁North", "▁Ca"]
    assert hold_back(t, 0) == 5  # "Ca" may be the start of "Carolina": it waits
    assert hold_back(t + ["ro", "li", "na", "."], 0) == 10  # a sentence end is whole
    assert hold_back(t + ["ro", "li", "na", ".", "▁"], 0) == 10  # a lone word mark waits
    assert hold_back(["▁Hi"], 0) == 1  # the only word is kept
    assert hold_back(t, 5) == 6
    assert hold_back(t, 6) == 6
    # heard long after it: the word is whole (its next piece would have come by now)
    assert hold_back(t, 0, [0.0, 0.4, 0.6, 0.9, 1.2, 1.6], heard_s=2.0) == 5
    assert hold_back(t, 0, [0.0, 0.4, 0.6, 0.9, 1.2, 1.6], heard_s=3.2) == 6


class StreamRecognizer:
    """A fake sherpa recogniser: one word per 0.5 s of loud audio, 0.5 s apart."""

    class Stream:
        def __init__(self):
            self.loud, self.tokens, self.times = 0, [], []

        def accept_waveform(self, rate, samples):
            self.loud += int(np.count_nonzero(samples > 0.01))  # after the level match
            while self.loud >= 8000 * (len(self.tokens) + 1):
                self.times.append(0.5 * len(self.tokens))
                self.tokens.append(f"▁w{len(self.tokens)}")

        def input_finished(self):
            pass

    def __init__(self):
        self.streams = 0

    def create_stream(self):
        self.streams += 1
        return self.Stream()

    def is_ready(self, stream):
        return False

    def get_result_all(self, stream):
        text = "".join(stream.tokens).replace("▁", " ")
        return SimpleNamespace(text=text, tokens=stream.tokens, timestamps=stream.times)


def test_a_split_in_talk_carries_on_the_stream_and_loses_no_word(config, bus):
    recognizer = StreamRecognizer()
    asr = NemotronASR({"flush_s": 0.4, "languages": ["en"]}, recognizer)
    s = service(config, bus, asr, soft_split_s=2.0)
    audio(s, 10.0, 2.5, SPEECH)  # w0..w4
    audio(s, 12.5, 0.15, QUIET)  # a breath: split here
    finals = transcripts(bus, final=True)
    assert [f["text"] for f in finals] == ["w0 w1 w2 w3"]  # w4 might still be growing
    audio(s, 12.65, 1.0, SPEECH)  # w5, w6
    audio(s, 13.65, 0.6, QUIET)  # the end
    finals = transcripts(bus, final=True)
    assert [f["text"] for f in finals] == ["w0 w1 w2 w3", "w4 w5 w6"]
    # one stream for both: the split did not start a fresh one
    assert recognizer.streams == 2  # the first, and the next one made after the end
    # word times go on from the first utterance's audio, never before its own start
    assert all(a >= finals[1]["t_start"] for _, a, _ in finals[1]["words"])


def test_after_a_split_the_next_frame_starts_speech_and_the_pause_counts():
    seg = Segmenter(
        {"vad_start": 0.5, "vad_end": 0.35, "min_speech_ms": 250, "end_silence_ms": 400}
    )
    seg.resume(0.2)
    active, _began, ended = seg.feed(1.0, 0.1)
    assert seg.start == 1.0 and seg.confirmed and not active and not ended
    for i in range(6):
        active, _began, ended = seg.feed(1.032 + 0.032 * i, 0.1)
    assert ended  # 0.2 s before the split and ~0.2 s after it: the utterance ends
    seg.resume(0.1)
    active, _, ended = seg.feed(2.0, 0.9)
    assert active and not ended and seg.silence_s == 0


def _db(frame):
    return 20 * np.log10(float(np.sqrt(np.mean(frame * frame))) + 1e-9)


def _tone(db, n=512):
    return (np.sin(np.arange(n) * 0.3) * np.sqrt(2) * 10 ** (db / 20)).astype(np.float32)


def test_quiet_speech_is_raised_for_the_vad_but_not_the_room_floor():
    gain = InputGain({})  # -26 dBFS target, +30 dB at most, floor kept under -45 dBFS
    rng = np.random.default_rng(0)
    floor = lambda: rng.normal(0, 10 ** (-65 / 20), 512).astype(np.float32)
    for _ in range(100):
        gain(floor())
    speech = [gain(_tone(-56) + floor()) for _ in range(40)]
    # the quiet talker comes out ~20 dB louder: the floor (-65) may rise to -45, no more
    assert 18 < _db(speech[-1]) - _db(_tone(-56)) < 21
    assert _db(gain(floor())) < -44


def test_a_loud_talker_gets_no_gain_and_a_sudden_loud_sound_drops_it_fast():
    gain = InputGain({"vad_gain_floor_dbfs": 0})  # no floor cap here
    for _ in range(100):
        gain(_tone(-56))
    assert gain.gain_db > 25
    loud = [gain(_tone(-20)) for _ in range(12)]
    assert gain.gain_db < 5  # a few frames (the 90th percentile needs ~5), not seconds
    assert np.max(np.abs(loud[0])) <= 1.0
    for _ in range(50):
        out = gain(_tone(-20))
    assert abs(_db(out) - _db(_tone(-20))) < 0.1


def test_the_vad_hears_the_gained_frame_when_it_is_on(config, bus):
    heard = []
    s = service(config, bus, GrowingASR(), vad_gain=True)
    s.vad = lambda frame: heard.append(_db(frame)) or 0.1
    rng = np.random.default_rng(0)
    for i in range(160):
        noise = rng.normal(0, 10 ** (-65 / 20), 512).astype(np.float32)
        tone = _tone(-56) if i >= 100 else 0  # the room's floor, then a quiet talker
        s._audio({"t": 10 + i * 0.032, "samples": tone + noise}, 0)
    assert heard[-1] > -40  # raised ~20 dB
    assert service(config, bus, GrowingASR()).vad_gain is not None  # on by default (A-31)
    assert service(config, bus, GrowingASR(), vad_gain=False).vad_gain is None


class QuietTalkerASR(GrowingASR):
    """Words for the first 1 s of audio, then nothing new: the talker stopped, but
    background talk keeps the VAD on."""

    def feed(self, samples, final=False):
        if len(samples) and sum(n for n, _ in self.fed) >= 16000:
            self.fed.append((len(samples), final))
            text = " ".join(f"w{i}" for i in range(self.words))
            return Recognition(text, "en", [])
        return super().feed(samples, final)


def test_background_talk_that_keeps_the_vad_on_still_lets_a_final_come(config, bus):
    s = service(config, bus, QuietTalkerASR(), soft_split_s=2.0, soft_split_word_gap_s=0.8)
    audio(s, 10.0, 4.0, SPEECH)  # never a VAD pause
    finals = transcripts(bus, final=True)
    assert len(finals) == 1
    # ~2 s in, and 0.8 s after the last new word (at ~1 s of audio with the pre-roll)
    assert 12.0 <= finals[0]["t_end"] <= 12.5
    s2 = service(config, bus := type(bus)(), QuietTalkerASR(), soft_split_s=2.0)
    audio(s2, 10.0, 4.0, SPEECH)
    assert transcripts(bus, final=True) == []  # off: only a VAD pause splits
