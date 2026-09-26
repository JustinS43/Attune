"""SpeechOutService fallback logic with fake voices and a fake player (no audio device)."""

import threading
import time

import numpy as np
import pytest
from attune.speech_out import elevenlabs_tts
from attune.speech_out.player import resample
from attune.speech_out.service import SpeechOutService
from conftest import wait_for


class FakeTTS:
    def __init__(
        self, name, delay=0.0, fail=False, chunks=3, block=None, fail_after=None
    ):
        self.name, self.delay, self.fail, self.chunks = name, delay, fail, chunks
        self.block, self.fail_after = block, fail_after
        self.sample_rate = 24000
        self.calls = []

    def stream(self, text, lang=None, cancel=None):
        self.calls.append(text)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise ConnectionError("offline")
        for i in range(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise ConnectionError("dropped")
            if self.block is not None:
                self.block.wait(2.0)
            if cancel is not None and cancel.is_set():
                return
            yield np.full(240, 0.1, np.float32)


class FakePlayer:
    def __init__(self, per_chunk_s=0.0):
        self.per_chunk_s = per_chunk_s
        self.played = []

    def play(self, chunks, sample_rate, cancel, on_start=None):
        n = 0
        for chunk in chunks:
            if cancel.is_set():
                break
            if n == 0 and on_start:
                on_start()
            n += 1
            time.sleep(self.per_chunk_s)
        self.played.append(n)
        return n * 0.01


@pytest.fixture
def make(bus):
    services = []

    def build(eleven=False, kokoro=None, player=None, fallback=0.3):
        service = SpeechOutService(
            bus,
            {"clock": time.monotonic, "speech_out": {"fallback_after_s": fallback}},
            elevenlabs=eleven,
            kokoro=kokoro if kokoro is not None else FakeTTS("kokoro"),
            player=player or FakePlayer(),
        )
        service.start()
        services.append(service)
        return service

    yield build
    for service in services:
        service.stop()


def speak(bus, text="Nice to meet you"):
    bus.publish("command", {"name": "speak", "args": {"text": text, "source": "typed"}})


def test_elevenlabs_first(bus, make):
    eleven = FakeTTS("elevenlabs")
    service = make(eleven=eleven)
    speak(bus)
    assert wait_for(lambda: len(bus.of("speech_out.playing")) == 2)
    assert bus.of("reply.spoken")[0]["voice"] == "elevenlabs"
    assert bus.of("reply.spoken")[0]["text"] == "Nice to meet you"
    assert [e["state"] for e in bus.of("speech_out.playing")] == ["start", "end"]
    assert service.kokoro.calls == []
    assert service.player.played == [3]


def test_slow_elevenlabs_falls_back_to_kokoro(bus, make):
    service = make(eleven=FakeTTS("elevenlabs", delay=1.0), fallback=0.2)
    t0 = time.monotonic()
    speak(bus)
    assert wait_for(lambda: bus.of("reply.spoken"))
    assert bus.of("reply.spoken")[0]["voice"] == "kokoro"
    assert time.monotonic() - t0 < 0.9
    assert service.metrics["fallbacks"] == 1


def test_failing_elevenlabs_falls_back_immediately(bus, make):
    make(eleven=FakeTTS("elevenlabs", fail=True), fallback=1.0)
    t0 = time.monotonic()
    speak(bus)
    assert wait_for(lambda: bus.of("reply.spoken"))
    assert bus.of("reply.spoken")[0]["voice"] == "kokoro"
    assert time.monotonic() - t0 < 0.5


def test_no_key_uses_kokoro(bus, make):
    make(eleven=False)
    speak(bus)
    assert wait_for(lambda: bus.of("reply.spoken"))
    assert bus.of("reply.spoken")[0]["voice"] == "kokoro"


def test_saved_credentials_apply_to_next_reply(bus, monkeypatch, tmp_path):
    """Settings saved after startup must not leave the offline voice selected."""
    from dotenv import set_key

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    created = []

    def from_env(_cls, _cfg):
        key, voice = elevenlabs_tts.load_credentials()
        if not key:
            return None
        result = FakeTTS("elevenlabs")
        created.append((voice, result))
        return result

    monkeypatch.setattr(elevenlabs_tts.ElevenLabsTTS, "from_env", classmethod(from_env))
    service = SpeechOutService(
        bus,
        {"clock": time.monotonic, "speech_out": {"fallback_after_s": 0.3}},
        kokoro=FakeTTS("kokoro"),
        player=FakePlayer(),
    )
    service.start()
    try:
        speak(bus, "before saving")
        assert wait_for(lambda: len(bus.of("reply.spoken")) == 1)
        assert bus.of("reply.spoken")[-1]["voice"] == "kokoro"

        path = tmp_path / ".env"
        set_key(path, "ELEVENLABS_API_KEY", "synthetic-key")
        set_key(path, "ELEVENLABS_VOICE_ID", "voice-one")
        speak(bus, "after saving")
        assert wait_for(lambda: len(bus.of("reply.spoken")) == 2)
        assert bus.of("reply.spoken")[-1]["voice"] == "elevenlabs"
        assert created[-1][0] == "voice-one"

        set_key(path, "ELEVENLABS_VOICE_ID", "voice-two")
        speak(bus, "after changing voice")
        assert wait_for(lambda: len(bus.of("reply.spoken")) == 3)
        assert created[-1][0] == "voice-two"
        assert len(created) == 2
    finally:
        service.stop()


def test_stream_breaking_midway_does_not_repeat(bus, make):
    service = make(eleven=FakeTTS("elevenlabs", chunks=5, fail_after=2))
    speak(bus)
    assert wait_for(lambda: len(bus.of("speech_out.playing")) == 2)
    assert service.kokoro.calls == []  # never speak the same reply twice
    assert service.player.played == [2]


def test_one_at_a_time_in_order(bus, make):
    make(eleven=False, player=FakePlayer(per_chunk_s=0.05))
    speak(bus, "one")
    speak(bus, "two")
    assert wait_for(lambda: len(bus.of("reply.spoken")) == 2)
    assert [e["text"] for e in bus.of("reply.spoken")] == ["one", "two"]
    states = [e["state"] for e in bus.of("speech_out.playing")]
    assert wait_for(lambda: len(bus.of("speech_out.playing")) == 4)
    states = [e["state"] for e in bus.of("speech_out.playing")]
    assert states == ["start", "end", "start", "end"]


def test_forget_interrupts(bus, make):
    make(
        eleven=False,
        kokoro=FakeTTS("kokoro", chunks=50),
        player=FakePlayer(per_chunk_s=0.05),
    )
    speak(bus, "a long reply")
    speak(bus, "queued")
    assert wait_for(lambda: bus.of("speech_out.playing"))
    bus.publish("session.forget", {})
    assert wait_for(lambda: len(bus.of("speech_out.playing")) == 2, timeout=1.0)
    time.sleep(0.3)
    assert [e["text"] for e in bus.of("reply.spoken")] == ["a long reply"]


def test_empty_text_ignored_and_other_commands(bus, make):
    make(eleven=False)
    speak(bus, "   ")
    bus.publish("command", {"name": "pattern.test", "args": {"name": "OK"}})
    time.sleep(0.3)
    assert bus.of("reply.spoken") == []


def test_no_voice_at_all(bus, make):
    service = make(eleven=False, kokoro=FakeTTS("kokoro", fail=True))
    speak(bus)
    assert wait_for(lambda: service.metrics["failed"] == 1)
    assert bus.of("speech_out.playing") == []
    assert wait_for(lambda: bus.of("status.part"), timeout=2.0)
    assert bus.of("status.part")[-1]["ok"] is False


def test_credentials_never_required(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    assert elevenlabs_tts.load_credentials()[0] is None
    assert elevenlabs_tts.ElevenLabsTTS.from_env({}) is None


def test_elevenlabs_pcm_conversion(monkeypatch):
    """Odd-sized network chunks are stitched into whole 16-bit samples."""
    tts = elevenlabs_tts.ElevenLabsTTS.__new__(elevenlabs_tts.ElevenLabsTTS)
    tts.voice_id, tts.model_id, tts.sample_rate = "v", "m", 24000
    raw = (np.array([0, 16384, -16384, 32767], "<i2")).tobytes()

    class Stream:
        def stream(self, voice_id, **kwargs):
            assert kwargs["output_format"] == "pcm_24000"
            yield raw[:3]
            yield raw[3:]

    tts._client = type("C", (), {"text_to_speech": Stream()})()
    out = np.concatenate(list(tts.stream("hi")))
    assert np.allclose(out, [0, 0.5, -0.5, 32767 / 32768])


@pytest.mark.parametrize("env_key", ["environment-key", "   ", ""])
def test_credentials_merge_env_and_dotenv(monkeypatch, tmp_path, env_key):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", env_key)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    (tmp_path / ".env").write_text(
        "ELEVENLABS_API_KEY=file-key\nELEVENLABS_VOICE_ID=file-voice\n"
    )
    key, voice = elevenlabs_tts.load_credentials()
    assert key == (env_key.strip() or "file-key")
    assert voice == "file-voice"


def test_environment_credentials_take_precedence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", " environment-key ")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", " environment-voice ")
    (tmp_path / ".env").write_text(
        "ELEVENLABS_API_KEY=file-key\nELEVENLABS_VOICE_ID=file-voice"
    )
    assert elevenlabs_tts.load_credentials() == ("environment-key", "environment-voice")


def test_cancelled_reply_does_not_contact_elevenlabs():
    tts = elevenlabs_tts.ElevenLabsTTS.__new__(elevenlabs_tts.ElevenLabsTTS)
    cancel = threading.Event()
    cancel.set()
    # No client: any attempt to contact the provider would fail this test.
    assert list(tts.stream("cancelled reply", cancel=cancel)) == []


def test_real_sdk_request_streams_only_reply(monkeypatch):
    """Exercise the installed SDK and HTTP serialization without contacting ElevenLabs."""
    import httpx
    from elevenlabs.client import ElevenLabs

    requests = []

    def respond(request):
        import json

        requests.append(request)
        body = json.loads(request.content)
        assert body["text"] == "Nice to meet you"
        assert body["model_id"] == "eleven_flash_v2_5"
        assert body["language_code"] == "es"
        assert set(body) == {"text", "model_id", "language_code"}
        assert request.url.params["output_format"] == "pcm_24000"
        assert request.url.path == "/v1/text-to-speech/test-voice/stream"
        return httpx.Response(
            200, content=np.array([0, 16384, -16384], "<i2").tobytes()
        )

    tts = elevenlabs_tts.ElevenLabsTTS.__new__(elevenlabs_tts.ElevenLabsTTS)
    tts.voice_id, tts.model_id, tts.sample_rate = (
        "test-voice",
        "eleven_flash_v2_5",
        24000,
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        tts._client = ElevenLabs(api_key="test-key", httpx_client=client)
        audio = np.concatenate(list(tts.stream("Nice to meet you", "es")))
    assert len(requests) == 1
    assert np.allclose(audio, [0, 0.5, -0.5])


@pytest.mark.parametrize("first_audio", [False, True])
def test_forget_releases_stalled_network_stream(bus, make, first_audio):
    entered = threading.Event()
    release = threading.Event()

    class StalledVoice:
        sample_rate = 24000

        def stream(self, text, lang=None, cancel=None):
            if first_audio:
                yield np.zeros(240, np.float32)
            entered.set()
            release.wait(5)

    service = make(eleven=StalledVoice(), fallback=4)
    try:
        speak(bus)
        assert entered.wait(1)
        if first_audio:
            assert wait_for(lambda: bus.of("reply.spoken"))
        bus.publish("session.forget", {})
        assert wait_for(lambda: not service.speaking, timeout=0.5)
        service.stop()
        assert not service._thread.is_alive()
        assert service.kokoro.calls == []
        states = [e["state"] for e in bus.of("speech_out.playing")]
        assert states == (["start", "end"] if first_audio else [])
    finally:
        release.set()


def test_resample_length():
    x = np.zeros(2400, np.float32)
    assert len(resample(x, 24000, 48000)) == 4800
    assert resample(x, 24000, 24000) is x


def test_kokoro_missing_model_reported(tmp_path):
    from attune.speech_out.kokoro_tts import KokoroTTS

    tts = KokoroTTS(tmp_path)
    assert not tts.available()
    assert "model.onnx" in tts.missing()
    with pytest.raises(FileNotFoundError):
        tts.load()


def test_interrupt_event_is_threadsafe(bus, make):
    gate = threading.Event()
    service = make(eleven=FakeTTS("elevenlabs", chunks=3, block=gate), fallback=1.0)
    speak(bus)
    time.sleep(0.1)
    service.interrupt()
    gate.set()
    time.sleep(0.3)
    assert bus.of("reply.spoken") == [] or len(bus.of("speech_out.playing")) in (0, 2)


# ---------------------------------------------------------------- device "none" (P-37)


@pytest.mark.parametrize("device", ["none", "NULL", " off ", "silent"])
def test_silent_device_names(device):
    from attune.speech_out.player import is_silent_device

    assert is_silent_device(device)


@pytest.mark.parametrize("device", [None, "", "Speakers (Realtek)", "default"])
def test_real_device_names(device):
    from attune.speech_out.player import is_silent_device

    assert not is_silent_device(device)


def test_null_player_times_like_audio_but_plays_nothing():
    from attune.speech_out.player import NullPlayer

    chunks = [np.zeros(2400, np.float32)] * 5  # 0.5 s at 24 kHz
    started = []
    t0 = time.monotonic()
    played = NullPlayer().play(
        iter(chunks), 24000, threading.Event(), lambda: started.append(1)
    )
    assert played == pytest.approx(0.5)
    assert started == [1]
    assert time.monotonic() - t0 >= 0.4  # paced like real playback, so "speaking" lasts


def test_null_player_stops_when_cancelled():
    from attune.speech_out.player import NullPlayer

    cancel = threading.Event()
    cancel.set()
    assert NullPlayer().play(iter([np.zeros(24000, np.float32)]), 24000, cancel) == 0.0
    assert NullPlayer(realtime=False).play(
        iter([np.zeros(2400, np.float32)]), 24000, threading.Event()
    ) == pytest.approx(0.1)


def test_device_none_speaks_through_the_null_player(bus):
    from attune.speech_out.player import NullPlayer

    service = SpeechOutService(
        bus,
        {"clock": time.monotonic, "speech_out": {"device": "none"}},
        elevenlabs=False,
        kokoro=FakeTTS("kokoro"),
    )
    service.start()
    try:
        assert isinstance(service.player, NullPlayer)
        speak(bus)
        assert wait_for(lambda: bus.of("reply.spoken"), 3.0)
        assert service.status()["metrics"]["output"] == "none"
    finally:
        service.stop()
