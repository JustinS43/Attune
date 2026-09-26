from types import SimpleNamespace

import attune.audio.mic as mic_module
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


def test_mic_name_selection_falls_back(config, monkeypatch):
    monkeypatch.setattr(mic_module.sys, "platform", "darwin")
    sd = SimpleNamespace(
        query_devices=lambda: [{"name": "Other", "max_input_channels": 1, "hostapi": 0}],
        query_hostapis=lambda: [{"name": "Core Audio"}],
    )
    mic = MicReader(config["audio"], lambda: 0, lambda e: None, sd)
    assert mic._device(False) is None


def test_mic_name_selection_falls_back_to_wasapi_default_on_windows(config, monkeypatch):
    monkeypatch.setattr(mic_module.sys, "platform", "win32")
    devices = [
        {"name": "Other (MME)", "max_input_channels": 1, "hostapi": 0},
        {"name": "Other (WASAPI)", "max_input_channels": 1, "hostapi": 1},
    ]
    hosts = [
        {"name": "MME", "default_input_device": 0},
        {"name": "Windows WASAPI", "default_input_device": 1},
    ]
    sd = SimpleNamespace(query_devices=lambda: devices, query_hostapis=lambda: hosts)
    mic = MicReader(config["audio"], lambda: 0, lambda e: None, sd)
    assert mic._device(False) == 1
    hosts[1]["default_input_device"] = -1
    with pytest.raises(RuntimeError):
        mic._device(False)


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


def test_nemotron_uses_per_stream_language_and_keeps_auto_drafts_unknown():
    class Stream:
        def __init__(self):
            self.options = {}

        def set_option(self, key, value):
            self.options[key] = value

        def accept_waveform(self, rate, data):
            pass

    class Recognizer:
        def create_stream(self):
            return Stream()

        def is_ready(self, stream):
            return False

        def get_result_all(self, stream):
            return {"text": "Hola", "tokens": ["▁Hola"], "timestamps": [0]}

    model = Recognizer()
    auto = NemotronASR({"languages": ["en", "es"]}, model)
    assert auto.stream.options == {"language": "auto"}
    assert auto.feed(np.ones(16000)).lang == "und"
    spanish = NemotronASR({"languages": ["es"]}, model)
    assert spanish.stream.options == {"language": "es"}
    assert spanish.feed(np.ones(16000)).lang == "es"
    spanish.reset()
    assert spanish.stream.options == {"language": "es"}
    model.get_result_all = lambda stream: {
        "text": "<es-ES>Hola",
        "tokens": ["<es-ES>", "▁Hola"],
        "timestamps": [0, 0.1],
    }
    result = auto.feed(np.ones(16000))
    assert (result.text, result.lang) == ("Hola", "es")
    assert result.words == [("Hola", 0.1, 2.0)]


def test_audio_tries_nemotron_for_multilingual_capture(config, bus, monkeypatch):
    used = []
    config["nemotron"] = {}
    monkeypatch.setattr(
        "attune.audio.service.NemotronASR", lambda cfg: used.append(cfg) or FakeASR()
    )
    service = AudioService(bus, config)
    assert isinstance(service._make_asr(["en", "es"]), FakeASR)
    assert used == [{"languages": ["en", "es"]}]


def test_failed_language_switch_keeps_working_recognizer_and_detector(config, bus, monkeypatch):
    old_language = SimpleNamespace(languages=["en"])
    service = AudioService(bus, config, asr=FakeASR(), language=old_language)
    old_asr = service.asr
    monkeypatch.setattr(
        "attune.audio.service.LanguageID", lambda langs: SimpleNamespace(languages=langs)
    )

    def unavailable(languages):
        raise FileNotFoundError("local model missing")

    monkeypatch.setattr(service, "_make_asr", unavailable)
    with pytest.raises(FileNotFoundError):
        service._handle("command", {"name": "languages.set", "args": {"langs": ["es"]}}, 0)
    assert service.asr is old_asr
    assert service.language is old_language
    assert service.languages == ["en"]


def test_stale_language_load_does_not_replace_current_state(config, bus, monkeypatch):
    old_asr, old_language = FakeASR(), SimpleNamespace(languages=["en"])
    service = AudioService(bus, config, asr=old_asr, language=old_language)
    monkeypatch.setattr(
        "attune.audio.service.LanguageID", lambda langs: SimpleNamespace(languages=langs)
    )

    def slow_load(languages):
        service.worker.generation += 1
        return FakeASR()

    monkeypatch.setattr(service, "_make_asr", slow_load)
    service._handle("command", {"name": "languages.set", "args": {"langs": ["es"]}}, 0)
    assert service.asr is old_asr and service.language is old_language


def test_voice_match_duration_excludes_endpoint_silence(config, bus):
    received = []
    voices = FakeVoices()
    voices.match = lambda pcm: received.append(pcm.copy()) or (None, 0.0)
    service = AudioService(
        bus, config, vad=lambda pcm: 0.9 if pcm.mean() else 0.1,
        asr=FakeASR(), voices=voices,
        language=SimpleNamespace(detect=lambda text, lang: lang), mic=False,
    )
    service._audio({"t": 0, "samples": np.ones(20 * 512, np.float32)}, 0)
    service._audio({"t": 0.640, "samples": np.zeros(13 * 512, np.float32)}, 0)
    assert len(received[-1]) == 20 * 512
    assert np.all(received[-1] == 1)
    assert not service.speech_audio


def test_enrollment_and_harvest_use_only_attributed_vad_speech(config, bus, tmp_path):
    received = []
    voices = VoicePrints(tmp_path, lambda pcm: received.append(pcm.copy()) or [1.0, 0.0], 0.5, 5, 1)
    service = AudioService(bus, config, voices=voices)
    service.clock = lambda: 0
    service._handle(
        "command", {"name": "enroll.start", "args": {"track_id": 1, "consent": True, "consent_t": 0}}, 0
    )
    service._handle(
        "enroll.result", {"part": "face", "person_id": "sam", "track_id": 1, "ok": True}, 0
    )
    service.ring.append(0, np.r_[np.ones(2 * 16000), np.zeros(6 * 16000), np.ones(2 * 16000)])
    service.speech_intervals.extend([(0, 2), (8, 10)])
    caption = {"speaker": {"kind": "face", "track_id": 1}, "final": True}
    service._handle("caption", caption | {"utt_id": "first", "words": [("Hi", 0, 10)]}, 0)
    assert not voices.enrolled  # Ten seconds elapsed, but only four were speech.
    service._handle("voice.harvest", {"person_id": "sam", "t0": 0, "t1": 10}, 0)
    assert len(received[-1]) == 4 * 16000
    service.ring.append(10, np.ones(16000))
    service.speech_intervals.append((10, 11))
    service._handle("caption", caption | {"utt_id": "second", "words": [("again", 10, 11)]}, 0)
    assert "sam" in voices.enrolled
    assert len(received[-1]) == 5 * 16000
    assert np.all(received[-1] == 1)
    service._handle("session.forget", {}, 0)
    assert not service.speech_intervals and not service.ring.blocks
    assert not voices.session and "sam" in voices.enrolled


def test_invalid_new_consent_clears_previous_request(config, bus):
    service = AudioService(bus, config)
    service.pending_consent = {"track_id": 1, "consent": True, "consent_t": 0}
    service._handle(
        "command", {"name": "enroll.start", "args": {"track_id": 2, "consent": False}}, 0
    )
    assert service.pending_consent is None


def test_voice_gallery_ignores_corrupt_or_unconsented_records(tmp_path):
    import json

    records = {
        "broken": "not JSON",
        "missing_time": json.dumps({"consent": True, "embedding": [1, 0]}),
        "nan_time": json.dumps({"consent": True, "consent_t": float("nan"), "embedding": [1, 0]}),
        "good": json.dumps({"consent": True, "consent_t": 0, "embedding": [1, 0]}),
    }
    for person, data in records.items():
        folder = tmp_path / person
        folder.mkdir()
        (folder / "voice.json").write_text(data)
    voices = VoicePrints(tmp_path, lambda pcm: [1, 0], 0.5, 5, 1)
    assert set(voices.enrolled) == {"good"}
    with pytest.raises(ValueError, match="consent"):
        voices.enroll("new", np.ones(80000), True, float("nan"))


def test_whisper_final_preserves_non_latin_spacing_and_clamps_word_times():
    model = SimpleNamespace(transcribe=lambda *args, **kwargs: (
        [SimpleNamespace(text="你好。", words=[
            SimpleNamespace(word="你", start=-0.1, end=0.4),
            SimpleNamespace(word="好。", start=1.1, end=2.0),
        ])], SimpleNamespace(language="zh")
    ))
    result = WhisperASR({"languages": ["zh"]}, model).feed(np.ones(16000), True)
    assert result.text == "你好。"
    assert result.words == [("你", 0, 0.4), ("好。", 1, 1)]
