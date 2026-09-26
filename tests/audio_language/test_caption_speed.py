"""A-22: words sooner, nothing lost (gaps, pauses, replies, long talk, very short replies)."""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from attune.audio.asr import NemotronASR, Recognition
from attune.audio.service import AudioService, _is_16k

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


class Voices:
    def __init__(self):
        self.calls = []

    def match(self, samples):
        self.calls.append(len(samples))
        return None, 0.0

    def forget(self):
        pass


def service(config, bus, asr, voices=None, **audio):
    cfg = config | {
        "audio": config["audio"] | {"asr_chunk_ms": 32, "pre_roll_ms": 320} | audio
    }
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


def test_a_capture_gap_finishes_the_utterance_instead_of_dropping_it(config, bus):
    s = service(config, bus, GrowingASR())
    audio(s, 10.0, 1.0, SPEECH)
    audio(s, 13.0, 0.5, QUIET)  # 2 s of audio never arrived
    finals = transcripts(bus, final=True)
    assert len(finals) == 1 and finals[0]["text"]
    assert finals[0]["t_start"] == pytest.approx(
        10.0
    )  # lead-in silence is not real time


@pytest.mark.parametrize(
    "topic, event",
    [
        ("paused", {"paused": True}),
        ("speech_out.playing", {"state": "start", "t": 11.0}),
        ("command", {"name": "languages.set", "args": {"langs": ["en"]}}),
    ],
)
def test_pausing_replying_or_switching_language_keeps_what_was_said(
    config, bus, topic, event
):
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
        assert final[0]["words"][0][1] == pytest.approx(
            final[0]["t_start"] + 0.03, abs=0.01
        )
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
    audio(
        s, 12.5, 0.1, QUIET
    )  # a short breath between sentences, far below end_silence
    audio(s, 12.6, 1.0, SPEECH)
    finals = transcripts(bus, final=True)
    assert len(finals) == 1
    audio(s, 13.6, 0.6, QUIET)
    finals = transcripts(bus, final=True)
    assert len(finals) == 2 and finals[0]["utt_id"] != finals[1]["utt_id"]


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
    bus.publish(
        "audio.block", {"t": 0.0, "sample_rate": 32000, "samples": np.zeros(320)}
    )
    assert s.worker.inbox.qsize() == 0
    bus.publish(
        "audio.block", {"t": 0.0, "sample_rate": 16000, "samples": np.zeros(160)}
    )
    assert s.worker.inbox.qsize() == 1
    s.worker.stop()
