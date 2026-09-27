"""A-32 cloud captions client, against a fake Google streaming client (no network, no keys).

The fake behaves like Speech-to-Text v1 `StreamingRecognize` with diarization: interim
results without speaker tags, final results that list every word of the stream so far with
(revised) tags, offsets from the start of the stream, and errors raised from the response
iterator. Credentials are obviously fake strings and must never appear in a log.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from attune.audio.cloud_diarize import (
    CloudDiarizeService,
    CloudSettings,
    Credentials,
    GoogleApi,
    error_kind,
    parse_response,
    read_credentials,
)

FAKE_KEY = "fake-google-key-for-tests"
RATE = 16000


# ---------------------------------------------------------------- fake Google
def gword(w, a, b, tag=0):
    return SimpleNamespace(
        word=w,
        start_time=timedelta(seconds=a),
        end_time=timedelta(seconds=b),
        speaker_tag=tag,
        speaker_label=str(tag) if tag else "",
    )


def gresult(words, end, final=True, conf=0.92):
    alt = SimpleNamespace(
        transcript=" ".join(w.word for w in words),
        confidence=conf if final else 0.0,
        words=words,
    )
    return SimpleNamespace(
        is_final=final,
        alternatives=[alt],
        result_end_time=timedelta(seconds=end),
        language_code="en-us",
    )


def gresponse(*results):
    return SimpleNamespace(results=list(results), error=None)


class ResourceExhausted(Exception):
    """Named like google.api_core.exceptions.ResourceExhausted (quota)."""


class ServiceUnavailable(Exception):
    """Named like google.api_core.exceptions.ServiceUnavailable (network)."""


class PermissionDenied(Exception):
    """Named like google.api_core.exceptions.PermissionDenied (a rejected key)."""


class OutOfRange(Exception):
    """Named like the error Google sends when a stream runs past its limit."""


class FakeCall:
    """One fake stream: consumes the audio requests and replies by audio time."""

    def __init__(self, api, kind, config, requests, steps):
        self.api, self.kind, self.config = api, kind, config
        self.requests, self.steps = requests, sorted(steps, key=lambda s: s[1])
        self.audio = bytearray()
        self.chunks = 0
        self.first_chunks: list[bytes] = []
        self.ended = threading.Event()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def responses(self):
        steps = list(self.steps)
        try:
            for chunk in self.requests:
                if self.cancelled:
                    return
                self.audio += chunk
                self.chunks += 1
                if len(self.first_chunks) < 64:
                    self.first_chunks.append(bytes(chunk))
                heard = len(self.audio) / 2 / RATE
                while steps and steps[0][1] <= heard + 1e-9:
                    kind, _, what = steps.pop(0)
                    if kind == "error":
                        raise what
                    yield what
            for (
                kind,
                _,
                what,
            ) in steps:  # half-closed: the rest arrives, then the stream ends
                if kind == "close":
                    yield what
        finally:
            self.ended.set()

    @property
    def seconds(self):
        return len(self.audio) / 2 / RATE


class FakeApi:
    def __init__(self, scripts=None, available=True, reject=()):
        self.scripts = list(scripts or [])  # steps for each stream in turn
        self.calls: list[FakeCall] = []
        self.clients: list[str] = []
        self.configs: list[str] = []
        self._available = available
        self.reject = set(
            reject
        )  # sign-in kinds whose streams fail with PermissionDenied

    def available(self):
        return self._available

    def client(self, creds, kind, settings):
        self.clients.append(kind)
        return SimpleNamespace(kind=kind)

    def streaming_config(self, settings, language):
        self.configs.append(language)
        return {"language": language, "model": settings.model_for(language)}

    def request(self, chunk):
        return chunk

    def recognize(self, client, config, requests):
        n = len(self.calls)
        steps = self.scripts[n] if n < len(self.scripts) else []
        if client.kind in self.reject:
            steps = [("error", 0.0, PermissionDenied("fake"))]
        call = FakeCall(self, client.kind, config, requests, steps)
        self.calls.append(call)
        return call.responses()


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def cloud_config(config, clock, **cloud):
    config = dict(config)
    config["clock"] = clock
    config["cloud"] = {
        "enabled": False,
        "stream_max_s": 290,
        "overlap_s": 3.0,
        "retry_s": [0.2, 0.2],
        "quota_retry_s": 0.3,
        "latency_fallback_ms": 2500,
        "latency_window": 3,
        "stall_s": 4.0,
        "connect_grace_s": 0.3,
        "idle_close_s": 30.0,
        "credentials_poll_s": 0.05,
        **cloud,
    }
    config["audio"] = {**config["audio"], "mute_after_reply_s": 0.5}
    return config


KEY_ONLY = Credentials(api_key=FAKE_KEY)


def make(bus, config, api, creds=KEY_ONLY, **cloud):
    clock = Clock()
    svc = CloudDiarizeService(
        bus,
        cloud_config(config, clock, **cloud),
        api=api,
        credentials=lambda: holder[0],
    )
    holder = [creds]
    svc.creds_holder = holder
    svc.test_clock = clock
    return svc


def wait_for(pred, timeout=3.0):
    def holds():
        try:
            return bool(pred())
        except (IndexError, KeyError):  # nothing published yet
            return False

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if holds():
            return True
        time.sleep(0.01)
    return holds()


def feed(bus, svc, t0, seconds, value=0.1, lag=0.0, block_s=0.01):
    """Publish 10 ms 16 kHz blocks from engine time t0; the clock follows (plus `lag`)."""
    n = round(seconds / block_s)
    for i in range(n):
        t = t0 + i * block_s
        svc.test_clock.t = t + block_s + lag
        bus.publish(
            "audio.block",
            {
                "t": t,
                "sample_rate": RATE,
                "samples": np.full(round(RATE * block_s), value, "f4"),
            },
        )
        if i % 10 == 9:
            time.sleep(0.002)  # let the pump keep up, as real time would
    return t0 + n * block_s


def feed_until(bus, svc, pred, t0=10.0, step_s=0.2, tries=40):
    """Keep the mic talking in short slices until `pred` holds (as a live mic would)."""
    t = t0
    for _ in range(tries):
        t = feed(bus, svc, t, step_s)
        if wait_for(pred, 0.1):
            return t
    assert pred()
    return t


def states(bus):
    return [e for topic, e in bus.events if topic == "cloud.state"]


def cloud_words(bus):
    return [e for topic, e in bus.events if topic == "speaker.cloud"]


def turn_on(bus, svc, **args):
    bus.publish("command", {"name": "cloud.set", "args": {"on": True, **args}})


@pytest.fixture
def running():
    started = []

    def start(svc):
        svc.start()
        started.append(svc)
        return svc

    yield start
    for svc in started:
        svc.stop()


# ---------------------------------------------------------------- off by default
def test_off_by_default_creates_no_client_and_sends_no_audio(
    bus, config, running, caplog
):
    api = FakeApi()
    svc = running(make(bus, config, api))
    feed(bus, svc, 100.0, 2.0)
    time.sleep(0.2)
    assert api.clients == [] and api.calls == []  # no network client was ever created
    assert svc.queued_audio == 0 and svc.sent_s == 0  # no audio was queued or sent
    assert states(bus) and states(bus)[-1]["enabled"] is False
    assert states(bus)[-1]["state"] == "off"
    assert not cloud_words(bus)
    assert FAKE_KEY not in caplog.text


def test_on_without_credentials_is_unavailable_and_sends_nothing(
    bus, config, running, caplog
):
    caplog.set_level(logging.INFO, logger="attune.audio.cloud_diarize")
    api = FakeApi()
    svc = running(make(bus, config, api, creds=Credentials()))
    turn_on(bus, svc)
    assert wait_for(lambda: states(bus)[-1]["state"] == "unavailable")
    feed(bus, svc, 100.0, 1.0)
    time.sleep(0.2)
    last = states(bus)[-1]
    assert last["enabled"] is True and last["reason"] == "credentials missing"
    assert last["credentials"] is False
    assert api.clients == [] and svc.sent_s == 0
    assert "cloud: credentials missing" in caplog.text


def test_a_missing_library_is_unavailable(bus, config, running):
    api = FakeApi(available=False)
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: states(bus)[-1]["reason"] == "library missing")
    feed(bus, svc, 100.0, 0.5)
    assert api.clients == [] and svc.sent_s == 0


# ---------------------------------------------------------------- words and tags
def conversation_script():
    """Two people: A says "hi there", B answers "hello"; Google revises one tag later."""
    return [
        ("resp", 0.6, gresponse(gresult([gword("hi", 0.2, 0.4)], 0.6, final=False))),
        (
            "resp",
            1.2,
            gresponse(
                gresult([gword("hi", 0.2, 0.4, 1), gword("there", 0.45, 0.8, 1)], 1.2)
            ),
        ),
        (
            "resp",
            2.4,
            gresponse(  # finals re-list the whole stream; "there" is re-tagged as speaker 2
                gresult(
                    [
                        gword("hi", 0.2, 0.4, 1),
                        gword("there", 0.45, 0.8, 2),
                        gword("hello", 1.5, 1.9, 2),
                    ],
                    2.4,
                )
            ),
        ),
    ]


def test_words_come_on_the_engine_clock_and_only_new_or_retagged(
    bus, config, running, caplog
):
    api = FakeApi([conversation_script()])
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: states(bus)[-1]["state"] == "connecting")
    feed(bus, svc, 50.0, 3.0, lag=0.3)
    assert wait_for(lambda: len(cloud_words(bus)) >= 2)
    first, second = cloud_words(bus)[:2]
    assert first["stream_id"] == second["stream_id"] == "cloud-1"
    # the interim result had no tags, so the first event is the first final
    assert [w[0] for w in first["words"]] == ["hi", "there"]
    assert tuple(first["words"][0][1:]) == (50.2, 50.4, "1")
    assert first["final"] is True and first["t_end"] == pytest.approx(51.2)
    assert first["confidence"] == pytest.approx(0.92)
    # the second final re-listed everything: only the re-tagged and the new word are sent
    assert [(w[0], w[3]) for w in second["words"]] == [("there", "2"), ("hello", "2")]
    assert second["latency_s"] >= 0.3  # the clock ran 0.3 s behind the audio, or more

    assert wait_for(lambda: states(bus)[-1]["state"] == "on")
    assert api.clients == ["api_key"] and api.configs == ["en-US"]
    assert FAKE_KEY not in caplog.text


def test_restart_before_the_limit_replays_the_overlap_and_numbers_afresh(
    bus, config, running
):
    second = [
        (  # the new stream heard the overlap again: its tags start from 1 for other people
            "resp",
            1.0,
            gresponse(gresult([gword("again", 0.1, 0.5, 1)], 1.0)),
        )
    ]
    api = FakeApi([[], second])
    svc = running(make(bus, config, api, stream_max_s=2.0, overlap_s=0.5))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    feed(bus, svc, 10.0, 3.0)
    assert wait_for(lambda: len(api.calls) == 2)
    assert wait_for(
        lambda: api.calls[0].ended.is_set()
    )  # the old stream was half-closed
    assert svc.restarts == 1
    # the new stream first got ~0.5 s of audio the old one had heard (overlap_s)
    assert wait_for(lambda: cloud_words(bus))
    ev = cloud_words(bus)[-1]
    assert ev["stream_id"] == "cloud-2"
    # its clock starts at the replayed audio: 10 + 2.0 - 0.5 (+ one 100 ms chunk of slack)
    assert ev["words"][0][1] == pytest.approx(10.0 + 2.0 - 0.5 + 0.1, abs=0.15)
    assert api.calls[0].seconds == pytest.approx(2.0, abs=0.15)


def test_stream_limit_error_just_starts_the_next_stream(bus, config, running):
    api = FakeApi([[("error", 0.5, OutOfRange("fake"))], []])
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    feed_until(bus, svc, lambda: len(api.calls) == 2)
    assert all(s["state"] != "fallback" for s in states(bus))


# ---------------------------------------------------------------- fallback
def test_quota_error_falls_back_at_once_then_reconnects(bus, config, running, caplog):
    api = FakeApi([[("error", 0.3, ResourceExhausted("fake quota detail"))], []])
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    t = feed(bus, svc, 10.0, 0.6)
    assert wait_for(lambda: states(bus)[-1]["state"] == "fallback")
    assert states(bus)[-1]["reason"] == "quota"
    assert "fake quota detail" not in caplog.text  # Google's text is never logged
    time.sleep(0.35)  # quota_retry_s
    feed(bus, svc, t, 1.0)
    assert wait_for(lambda: len(api.calls) == 2)
    assert wait_for(lambda: states(bus)[-1]["state"] == "on")


def test_network_error_falls_back_and_retries(bus, config, running):
    api = FakeApi([[("error", 0.3, ServiceUnavailable("fake"))], []])
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    t = feed(bus, svc, 10.0, 0.6)
    assert wait_for(lambda: states(bus)[-1]["reason"] == "network")
    time.sleep(0.25)
    feed(bus, svc, t, 1.0)
    assert wait_for(lambda: states(bus)[-1]["state"] == "on")


def test_rejected_key_tries_the_service_account_then_reports_rejected(
    bus, config, running
):
    api = FakeApi(reject={"api_key"})
    creds = Credentials(api_key=FAKE_KEY, service_file="fake-service-account.json")
    svc = running(make(bus, config, api, creds=creds))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    t = 10.0
    for _ in range(30):
        t = feed(bus, svc, t, 0.2)
        if wait_for(lambda: api.clients[:2] == ["api_key", "service"], 0.1):
            break
    assert api.clients[:2] == ["api_key", "service"]
    feed(bus, svc, t, 1.0)
    assert wait_for(lambda: states(bus)[-1]["state"] == "on")
    # both refused: unavailable, and the audio stops at once
    api2 = FakeApi(reject={"api_key", "service"})
    bus2 = type(bus)()
    svc2 = running(make(bus2, config, api2, creds=creds))
    turn_on(bus2, svc2)
    assert wait_for(lambda: svc2.accepting)
    t = 10.0
    for _ in range(30):  # the mic keeps talking, as it would live
        t = feed(bus2, svc2, t, 0.2)
        if wait_for(lambda: states(bus2)[-1]["reason"] == "credentials rejected", 0.1):
            break
    assert states(bus2)[-1]["reason"] == "credentials rejected"
    assert not svc2.accepting


def test_slow_results_fall_back_and_recover(bus, config, running):
    slow = [
        (
            "resp",
            0.5 + 0.5 * k,
            gresponse(gresult([gword("w", 0.1, 0.3, 1)], 0.5 + 0.5 * k)),
        )
        for k in range(6)
    ]
    api = FakeApi([slow])
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    feed(bus, svc, 10.0, 3.2, lag=3.0)  # every result arrives 3 s after its audio
    assert wait_for(lambda: states(bus)[-1]["state"] == "fallback")
    assert states(bus)[-1]["reason"] == "slow"
    assert states(bus)[-1]["latency_ms"] >= 2500


def test_speech_without_results_stalls_to_fallback(bus, config, running):
    api = FakeApi([[]])
    svc = running(make(bus, config, api, stall_s=0.5))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    feed(bus, svc, 10.0, 0.4)
    assert wait_for(
        lambda: api.calls
    )  # the stream is open (opening resets the stall watch)
    bus.publish("audio.vad", {"t": 10.3, "is_speech": True, "prob": 0.9})
    feed(bus, svc, 10.4, 1.0)
    assert wait_for(lambda: states(bus)[-1]["reason"] == "slow")


# ---------------------------------------------------------------- privacy while on
def test_pause_closes_the_stream_and_sends_nothing(bus, config, running):
    api = FakeApi()
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    t = feed(bus, svc, 10.0, 0.5)
    assert wait_for(lambda: len(api.calls) == 1)
    bus.publish("paused", {"paused": True})
    assert wait_for(lambda: states(bus)[-1]["state"] == "paused")
    assert wait_for(lambda: api.calls[0].ended.is_set())
    sent = svc.sent_s
    t = feed(bus, svc, t, 1.0)
    time.sleep(0.1)
    assert svc.sent_s == sent and svc.queued_audio == 0
    bus.publish("paused", {"paused": False})
    feed(bus, svc, t + 5, 0.5)
    assert wait_for(lambda: len(api.calls) == 2)


def test_our_own_spoken_reply_is_sent_as_silence(bus, config, running):
    api = FakeApi()
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    bus.publish("speech_out.playing", {"state": "start", "t": 10.0})
    feed(bus, svc, 10.0, 0.5, value=0.5)
    assert wait_for(lambda: api.calls and api.calls[0].chunks >= 4)
    heard = np.frombuffer(bytes(api.calls[0].audio), "<i2")
    assert heard.size and not heard.any()  # silence, never the reply
    bus.publish("speech_out.playing", {"state": "end", "t": 10.5})
    feed(bus, svc, 10.5, 1.2, value=0.5)  # 0.5 s more of silence, then the room again
    assert wait_for(lambda: np.frombuffer(bytes(api.calls[0].audio), "<i2").any())


def test_session_forget_starts_a_fresh_stream_without_overlap(bus, config, running):
    api = FakeApi()
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    t = feed(bus, svc, 10.0, 1.0)
    bus.publish("session.forget", {})
    time.sleep(0.1)
    feed(bus, svc, t, 0.5)
    assert wait_for(lambda: len(api.calls) == 2)
    assert wait_for(lambda: api.calls[1].seconds > 0)
    assert api.calls[1].seconds <= 0.6  # nothing heard before the forget is sent again


# ---------------------------------------------------------------- the wearer's choice
def test_choice_and_language_are_kept_on_the_laptop(bus, config, running, tmp_path):
    api = FakeApi()
    svc = running(make(bus, config, api))
    turn_on(bus, svc, language="es-US")
    assert wait_for(lambda: states(bus)[-1]["language"] == "es-US")
    saved = json.loads((tmp_path / "cloud.json").read_text())
    assert saved == {"enabled": True, "language": "es-US"}
    assert states(bus)[-1]["languages"] == ["en-US", "es-US"]
    assert states(bus)[-1]["model"] == "command_and_search"
    svc.stop()
    bus2 = type(bus)()
    again = running(make(bus2, config, FakeApi()))
    assert wait_for(lambda: states(bus2) and states(bus2)[-1]["enabled"] is True)
    assert again.language == "es-US"
    bus2.publish("command", {"name": "cloud.set", "args": {"on": False}})
    assert wait_for(lambda: states(bus2)[-1]["state"] == "off")
    assert json.loads((tmp_path / "cloud.json").read_text())["enabled"] is False


def test_an_unknown_language_or_bad_command_is_ignored(bus, config, running):
    api = FakeApi()
    svc = running(make(bus, config, api))
    bus.publish("command", {"name": "cloud.set", "args": {"on": "yes"}})
    bus.publish(
        "command", {"name": "cloud.set", "args": {"on": True, "language": "xx-XX"}}
    )
    assert wait_for(lambda: states(bus)[-1]["enabled"] is True)
    assert svc.language == "en-US"


def test_a_key_saved_later_applies_without_a_restart(bus, config, running):
    api = FakeApi()
    svc = running(make(bus, config, api, creds=Credentials()))
    turn_on(bus, svc)
    assert wait_for(lambda: states(bus)[-1]["reason"] == "credentials missing")
    svc.creds_holder[0] = Credentials(api_key=FAKE_KEY)  # typed into Settings
    assert wait_for(lambda: svc.accepting)
    feed(bus, svc, 10.0, 0.5)
    assert wait_for(lambda: api.clients == ["api_key"])
    assert states(bus)[-1]["credentials"] is True


def test_turning_it_off_cancels_the_stream_and_drops_audio(bus, config, running):
    api = FakeApi()
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    t = feed(bus, svc, 10.0, 0.5)
    assert wait_for(lambda: len(api.calls) == 1)
    bus.publish("command", {"name": "cloud.set", "args": {"on": False}})
    assert wait_for(lambda: states(bus)[-1]["state"] == "off")
    assert wait_for(lambda: api.calls[0].ended.is_set())
    sent = svc.sent_s
    feed(bus, svc, t, 0.5)
    time.sleep(0.1)
    assert svc.sent_s == sent and len(api.calls) == 1


def test_status_part_reports_state_latency_and_audio_sent(bus, config, running):
    api = FakeApi([conversation_script()])
    svc = running(make(bus, config, api))
    turn_on(bus, svc)
    assert wait_for(lambda: svc.accepting)
    feed(bus, svc, 10.0, 2.5, lag=0.2)
    svc.test_clock.t += 1.1
    assert wait_for(
        lambda: any(
            e["part"] == "cloud" and e["metrics"]["sent_s"] > 0
            for topic, e in bus.events
            if topic == "status.part"
        )
    )
    part = [
        e for topic, e in bus.events if topic == "status.part" and e["part"] == "cloud"
    ][-1]
    assert set(part["metrics"]) >= {"state", "latency_ms", "restarts", "sent_s"}


# ---------------------------------------------------------------- pieces
def test_read_credentials_env_first_then_dotenv_and_a_real_file_only(tmp_path):
    env_file = tmp_path / ".env"
    key_file = tmp_path / "sa.json"
    env_file.write_text(
        f"GOOGLE_SPEECH_API_KEY='{FAKE_KEY}'\nGOOGLE_APPLICATION_CREDENTIALS=nope\n"
    )
    creds = read_credentials({}, str(env_file))
    assert (
        creds.api_key == FAKE_KEY and creds.service_file is None
    )  # "nope" doesn't exist
    key_file.write_text("{}")
    creds = read_credentials(
        {"GOOGLE_APPLICATION_CREDENTIALS": str(key_file)}, str(env_file)
    )
    assert creds.kinds() == ["api_key", "service"]
    assert FAKE_KEY not in repr(creds) and str(key_file) not in repr(creds)
    assert read_credentials({}, str(tmp_path / "missing.env")).present is False
    assert creds.fingerprint() != Credentials().fingerprint()


def test_error_kinds_by_google_exception_name():
    assert error_kind(PermissionDenied()) == "auth"
    assert error_kind(ResourceExhausted()) == "quota"
    assert error_kind(OutOfRange()) == "limit"
    assert error_kind(ServiceUnavailable()) == "network"
    assert error_kind(ConnectionError()) == "network"


def test_settings_ignore_the_fusion_keys_in_the_same_table():
    s = CloudSettings.from_config(
        {"enabled": True, "final_wait_ms": 1200, "bind_min_s": 0.6}
    )
    assert s.enabled is True and s.languages == ["en-US", "es-US"]


def test_parses_real_google_v1_messages():
    speech = pytest.importorskip("google.cloud.speech_v1")
    resp = speech.StreamingRecognizeResponse(
        results=[
            speech.StreamingRecognitionResult(
                is_final=True,
                result_end_time=timedelta(seconds=2.5),
                language_code="en-us",
                alternatives=[
                    speech.SpeechRecognitionAlternative(
                        transcript="hi there",
                        confidence=0.9,
                        words=[
                            speech.WordInfo(
                                word="hi",
                                start_time=timedelta(seconds=0.2),
                                end_time=timedelta(seconds=0.4),
                                speaker_label="1",
                            ),
                            speech.WordInfo(
                                word="there",
                                start_time=timedelta(seconds=0.5),
                                end_time=timedelta(seconds=0.9),
                                speaker_tag=2,
                            ),
                        ],
                    )
                ],
            )
        ]
    )
    (res,) = parse_response(resp)
    assert res.final and res.end == pytest.approx(2.5)
    assert res.words == [("hi", 0.2, 0.4, "1"), ("there", 0.5, 0.9, "2")]
    cfg = GoogleApi().streaming_config(
        CloudSettings(min_speakers=2, max_speakers=4), "en-US"
    )
    assert cfg.interim_results is True
    rc = cfg.config
    assert rc.language_code == "en-US" and rc.model == "latest_long"
    assert rc.sample_rate_hertz == 16000 and rc.enable_word_time_offsets
    assert rc.diarization_config.enable_speaker_diarization
    assert (
        rc.diarization_config.min_speaker_count,
        rc.diarization_config.max_speaker_count,
    ) == (
        2,
        4,
    )
    req = GoogleApi().request(b"\x00\x00" * 1600)
    assert len(req.audio_content) == 3200
