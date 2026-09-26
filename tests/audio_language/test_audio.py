from types import SimpleNamespace

import numpy as np
import pytest
from attune.audio.asr import NemotronASR, Recognition, token_words
from attune.audio.asr_whisper import WhisperASR
from attune.audio.mic import AudioRing, MicReader
from attune.audio.service import AudioService
from attune.audio.vad import Segmenter
from attune.audio.voiceprint import VoicePrints


def test_ring_exact_span_expiry_and_gaps():
    ring = AudioRing(seconds=1, rate=10)
    ring.append(0, np.arange(10))
    ring.append(1, np.arange(10, 20))
    np.testing.assert_equal(ring.span(1.2, 1.5), [12, 13, 14])
    with pytest.raises(ValueError):
        ring.span(0, 1)
    ring.append(2.1, np.arange(10))
    with pytest.raises(ValueError):
        ring.span(1.8, 2.3)
    ring.clear()
    with pytest.raises(ValueError):
        ring.span(2.1, 2.4)


def test_ring_trims_partial_blocks_and_oversized_input():
    ring = AudioRing(seconds=1, rate=10)
    ring.append(0, np.arange(7))
    ring.append(0.7, np.arange(7, 14))
    assert sum(len(samples) for _, samples in ring.blocks) == 10
    np.testing.assert_equal(ring.span(0.4, 1.4), np.arange(4, 14))
    with pytest.raises(ValueError):
        ring.span(0, 0.5)
    ring.append(1.4, np.arange(30))
    assert sum(len(samples) for _, samples in ring.blocks) == 10
    np.testing.assert_equal(ring.span(3.4, 4.4), np.arange(20, 30))


@pytest.mark.parametrize("restart", [0.0, 0.5])
def test_ring_restart_discards_overlapping_old_audio(restart):
    ring = AudioRing(seconds=30, rate=10)
    ring.append(0, np.ones(10))
    ring.append(restart, np.full(10, 2))
    np.testing.assert_equal(ring.span(restart, restart + 1), np.full(10, 2))
    assert len(ring.blocks) == 1


def test_ring_empty_blocks_do_not_evict_audio_and_invalid_times_are_rejected():
    ring = AudioRing(seconds=1, rate=10)
    ring.append(0, np.arange(10))
    ring.append(100, np.empty(0))
    np.testing.assert_equal(ring.span(0, 1), np.arange(10))
    with pytest.raises(ValueError):
        ring.append(float("nan"), np.ones(10))
    with pytest.raises(ValueError):
        ring.span(0, float("inf"))


def test_vad_hysteresis_short_blip_and_silence(config):
    s = Segmenter(config["audio"])
    for i in range(5):
        s.feed(i * 0.032, 0.9)
    assert not s.confirmed
    for i in range(13):
        _, _, end = s.feed(0.16 + i * 0.032, 0.1)
    assert end and not s.confirmed
    s.reset()
    for i in range(8):
        active, _, _ = s.feed(i * 0.032, 0.9 if i == 0 else 0.4)
        assert active
    assert s.confirmed
    for i in range(12):
        assert not s.feed(0.256 + i * 0.032, 0.1)[2]
    assert s.feed(0.640, 0.1)[2]


def test_voice_consent_match_forget_delete(tmp_path):
    v = VoicePrints(tmp_path, lambda x: np.array([1.0, 0.0]), 0.5, 5, 1)
    with pytest.raises(ValueError):
        v.enroll("maya", np.ones(80000), False, 1.0)
    with pytest.raises(ValueError):
        v.enroll("../escape", np.ones(80000), True, 1.0)
    v.enroll("maya", np.ones(80000), True, 1.0)
    v.harvest("sam", np.ones(16000))
    assert "sam" not in [p.name for p in tmp_path.iterdir()]
    assert v.match(np.ones(100))[0] is None
    assert v.match(np.ones(16000))[0] in {"maya", "sam"}
    v.forget()
    assert not v.session and "maya" in v.enrolled
    assert (
        VoicePrints(tmp_path, lambda x: np.array([1.0, 0.0]), 0.5, 5, 1).match(np.ones(16000))[0]
        == "maya"
    )
    v.delete("maya")
    assert not (tmp_path / "maya" / "voice.json").exists()


def test_sentencepiece_times_are_real_and_clamped():
    assert token_words(["▁Hello", "▁wor", "ld"], [0.1, 0.3, 0.4], 0.5) == [
        ("Hello", 0.1, 0.3),
        ("world", 0.3, 0.5),
    ]
    assert token_words(["a"], [], 1) == []


def test_whisper_local_agreement():
    class Model:
        def __init__(self):
            self.calls = 0

        def transcribe(self, audio, **kw):
            self.calls += 1
            words = [
                SimpleNamespace(word=w, start=i * 0.1, end=(i + 1) * 0.1)
                for i, w in enumerate(["Hello", "world" if self.calls == 1 else "there"])
            ]
            return [SimpleNamespace(words=words)], SimpleNamespace(language="en")

    asr = WhisperASR({"languages": ["en"]}, Model())
    assert asr.feed(np.ones(16000)).text == ""
    assert asr.feed(np.ones(16000)).text == "Hello"
    assert asr.feed(np.ones(16000), True).text == "Hello there"


def test_nemotron_feeds_actual_pcm_and_flushes():
    class Stream:
        def __init__(self):
            self.blocks = []

        def accept_waveform(self, rate, data):
            self.blocks.append(data)

        def input_finished(self):
            self.finished = True

    class Recognizer:
        def create_stream(self):
            return Stream()

        def is_ready(self, s):
            return False

        def get_result_all(self, s):
            return {"text": "Hello", "tokens": ["▁Hello"], "timestamps": [0.1]}

    asr = NemotronASR({"flush_s": 0.8}, Recognizer())
    result = asr.feed(np.ones(16000), True)
    assert result.words == [("Hello", 0.1, 1.0)]
    assert asr.stream.finished
    assert len(asr.stream.blocks[-1]) == 12800


def test_mic_name_selection_falls_back(config):
    sd = SimpleNamespace(
        query_devices=lambda: [{"name": "Other", "max_input_channels": 1, "hostapi": 0}],
        query_hostapis=lambda: [{"name": "Core Audio"}],
    )
    mic = MicReader(config["audio"], lambda: 0, lambda e: None, sd)
    assert mic._device(False) is None


class FakeASR:
    def reset(self):
        pass

    def feed(self, samples, final=False):
        return Recognition("hello", "en", [("hello", 0, 0.1)])


class FakeVoices:
    def match(self, samples):
        return None, 0.0

    def forget(self):
        pass


def test_audio_service_mute_and_forget(config, bus):
    service = AudioService(
        bus,
        config,
        vad=lambda x: 0.9,
        asr=FakeASR(),
        voices=FakeVoices(),
        language=SimpleNamespace(detect=lambda text, lang: lang),
        mic=False,
    )
    service.start()
    try:
        service._handle("speech_out.playing", {"state": "start", "t": 0}, 0)
        service._audio({"t": 0.1, "samples": np.ones(16000)}, 0)
        assert not [e for t, e in bus.events if t == "audio.transcript"]
        service._handle("speech_out.playing", {"state": "end", "t": 1.0}, 0)
        service._audio({"t": 1.4, "samples": np.ones(1600)}, 0)
        assert not service.ring.blocks
        service._audio({"t": 1.5, "samples": np.ones(16000)}, 0)
        assert [e for t, e in bus.events if t == "audio.transcript"]
        service._handle("session.forget", {}, 0)
        assert not service.ring.blocks and not service.utterance
    finally:
        service.stop()
    assert not service.worker.thread.is_alive()


def test_enrollment_requires_matching_face_result(config, bus):
    service = AudioService(bus, config)
    service.clock = lambda: 0.0
    service._handle(
        "command",
        {"name": "enroll.start", "args": {"track_id": 1, "consent": True, "consent_t": 0}},
        0,
    )
    service._handle(
        "enroll.result", {"person_id": "x", "part": "face", "ok": True, "track_id": 2}, 0
    )
    assert service.enrollment is None
    service._handle(
        "enroll.result", {"person_id": "x", "part": "face", "ok": True, "track_id": 1}, 0
    )
    assert service.enrollment["person_id"] == "x"


def test_voice_enrollment_invalidated_during_inference(tmp_path):
    v = VoicePrints(tmp_path, lambda x: np.array([1.0, 0.0]), 0.5, 5, 1)
    v.enroll("sam", np.ones(80000), True, 1.0, guard=lambda: False)
    assert not (tmp_path / "sam" / "voice.json").exists()
    assert not v.enrolled


def test_audio_final_follows_silence_with_shared_timestamps(config, bus):
    service = AudioService(
        bus,
        config,
        vad=lambda x: 0.9 if x.mean() > 0.1 else 0.1,
        asr=FakeASR(),
        voices=FakeVoices(),
        language=SimpleNamespace(detect=lambda text, lang: lang),
        mic=False,
    )
    service.start()
    try:
        service._audio({"t": 10.0, "samples": np.ones(16000, np.float32)}, 0)
        service._audio({"t": 11.0, "samples": np.zeros(8000, np.float32)}, 0)
        transcripts = [e for t, e in bus.events if t == "audio.transcript"]
        assert transcripts[-1]["final"] is True
        assert transcripts[-1]["t_start"] == 10.0
        assert transcripts[-1]["words"][0][1] == 10.0
        assert len({t["utt_id"] for t in transcripts}) == 1
    finally:
        service.stop()


def test_utterance_level_does_not_raise_trailing_noise():
    from attune.audio.asr import UtteranceLevel

    level = UtteranceLevel(0.1)
    np.testing.assert_allclose(level.feed(np.full(16000, 0.5, np.float32)), 0.1)
    np.testing.assert_allclose(level.feed(np.full(8000, 0.0001, np.float32)), 0.00002)
    assert np.max(np.abs(level.feed(np.ones(100, np.float32)))) <= 0.99
    level.reset()
    np.testing.assert_allclose(level.feed(np.full(16000, 0.05, np.float32)), 0.1)


def test_runtime_nemotron_failure_replays_whole_utterance_to_whisper(config, bus, monkeypatch):
    class BrokenNemotron(NemotronASR):
        def __init__(self):
            self.calls = 0

        def reset(self):
            pass

        def feed(self, pcm, final=False):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("decoder failed")
            return Recognition("draft", "en", [])

    received = []

    class Fallback:
        def __init__(self, cfg):
            assert cfg["languages"] == ["en"]

        def reset(self):
            pass

        def feed(self, pcm, final=False):
            received.append((pcm.copy(), final))
            return Recognition("recovered", "en", [])

    monkeypatch.setattr("attune.audio.service.WhisperASR", Fallback)
    config["whisper"] = {}
    service = AudioService(bus, config, asr=BrokenNemotron())
    assert service._recognize(np.full(1000, 0.5, np.float32), False).text == "draft"
    assert service._recognize(np.full(500, 0.01, np.float32), True).text == "recovered"
    assert received[0][1] is True
    assert len(received[0][0]) == 1500
    np.testing.assert_allclose(received[0][0][:1000], 0.1)
    np.testing.assert_allclose(received[0][0][1000:], 0.002)
    assert isinstance(service.asr, Fallback)


def test_failed_fallback_clears_partial_audio(config, bus, monkeypatch):
    class BrokenNemotron(NemotronASR):
        def __init__(self):
            pass

        def reset(self):
            pass

        def feed(self, pcm, final=False):
            raise RuntimeError("decoder failed")

    def missing_model(cfg):
        raise FileNotFoundError("local Whisper model unavailable")

    monkeypatch.setattr("attune.audio.service.WhisperASR", missing_model)
    config["whisper"] = {}
    service = AudioService(bus, config, asr=BrokenNemotron())
    service.utterance.append(np.ones(100))
    with pytest.raises(FileNotFoundError):
        service._recognize(np.ones(1000, np.float32), True)
    assert not service.normalized
    assert not service.utterance
    assert service.level.gain is None


def test_mic_start_failure_stops_audio_worker_and_unsubscribes(config, bus):
    class BrokenMic:
        def health(self):
            return {"ok": False, "detail": "missing device"}

        def start(self):
            raise RuntimeError("cannot open capture")

        def stop(self):
            self.stopped = True

    mic = BrokenMic()
    service = AudioService(
        bus,
        config,
        vad=lambda pcm: 0.9,
        asr=FakeASR(),
        voices=FakeVoices(),
        language=SimpleNamespace(detect=lambda text, lang: lang),
        mic=mic,
    )
    with pytest.raises(RuntimeError, match="capture"):
        service.start()
    assert mic.stopped
    assert not service.worker.thread.is_alive()
    assert not any(bus.callbacks.values())
