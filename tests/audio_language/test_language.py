import json
import threading
import time
from concurrent.futures import Future

import numpy as np
import pytest
from attune.llm.client import OllamaClient
from attune.llm.describe import Descriptions, png
from attune.llm.names import Names
from attune.llm.replies import result as reply_result
from attune.llm.service import LLMService
from attune.llm.translate import result as translation_result


def caption(text="I'm Sam", track=1, kind="face"):
    return {
        "utt_id": "u1",
        "text": text,
        "final": True,
        "lang": "en",
        "speaker": {"kind": kind, "track_id": track, "person_id": None},
    }


@pytest.mark.parametrize(
    "text", ["I'm tired", "I'm starving", "Soy estudiante", "I'm Sam's sister"]
)
def test_name_traps_fail_even_if_model_calls_them_names(config, text):
    n = Names(config["llm"])
    guessed = text.split(maxsplit=1)[1]
    answer = {"is_intro": True, "name": guessed, "whose": "speaker", "confidence": 0.99}
    assert n.propose(caption(text), answer, 0) is None


def test_name_lifecycle_expiry_and_wrong_speaker(config):
    names = Names(config["llm"])
    answer = {"is_intro": True, "name": "Sam", "whose": "speaker", "confidence": 0.9}
    assert not names.eligible(caption(kind="you"))
    assert names.propose(caption(), answer | {"whose": "other"}, 0) is None
    proposal = names.propose(caption(), answer, 0)
    assert names.answer(proposal["proposal_id"], True, 11)["state"] == "expired"
    proposal = names.propose(caption(), answer, 12)
    assert names.answer(proposal["proposal_id"], True, 13)["state"] == "confirmed"
    assert not names.eligible(caption())
    names.forget()
    assert names.eligible(caption())


def test_description_allowlist_duplicates_and_png():
    d = Descriptions()
    assert d.result(1, {"color": "young", "garment": "shirt"}) is None
    answer = {"color": "blue", "garment": "jacket", "accessory": None}
    assert d.result(1, answer)["label"] == "Person in blue jacket"
    assert d.result(2, answer)["label"] == "Person in blue jacket, 2"
    assert d.result(3, answer | {"accessory": "hat"})["label"] == "Person in blue jacket, hat"
    import base64

    assert base64.b64decode(png(np.zeros((2, 3, 3), np.uint8))).startswith(b"\x89PNG")


def test_translation_and_reply_validation():
    c = caption() | {"lang": "es"}
    assert translation_result(c, {"text_en": " Hello "}) == {
        "utt_id": "u1",
        "source_lang": "es",
        "text_en": "Hello",
    }
    assert translation_result(c, {"text_en": 3}) is None
    assert reply_result({"options": ["yes", "yes", "no"]}) is None
    assert reply_result({"options": ["Yes", "No", "Please repeat"]})["options"][0] == "Yes"


def test_ollama_priority_payload_and_cancel(config):
    calls = []

    def transport(payload):
        calls.append(payload)
        return {"message": {"content": json.dumps({"ok": True})}}

    client = OllamaClient(config["llm"], transport)
    description = client.submit("descriptions", [{"content": "clothes"}], {})
    name = client.submit("names", [{"content": "name"}], {})
    translation = client.submit("translation", [{"content": "translate"}], {})
    client.start()
    try:
        for future in (description, name, translation):
            assert future.result(timeout=1) == {"ok": True}
        assert calls[0]["messages"][0]["content"] == "translate"
        assert calls[0]["think"] is False and calls[0]["keep_alive"] == -1
        assert calls[-1]["messages"][0]["content"] == "clothes"
    finally:
        client.stop()
    assert not client.thread.is_alive()


def test_forget_invalidates_inflight_ollama(config):
    entered, release = threading.Event(), threading.Event()

    def transport(payload):
        entered.set()
        release.wait(1)
        return {"message": {"content": '{"text_en":"private"}'}}

    client = OllamaClient(config["llm"], transport)
    future = client.submit("translation", [], {})
    client.start()
    assert entered.wait(1)
    client.cancel_pending()
    release.set()
    deadline = time.monotonic() + 1
    while not future.done() and time.monotonic() < deadline:
        time.sleep(0.01)
    client.stop()
    assert future.cancelled()


def test_service_forget_drops_language_results(config, bus):
    class Client:
        def submit(self, *args):
            self.future = Future()
            return self.future

        def cancel_pending(self):
            pass

    client = Client()
    s = LLMService(bus, config, client=client)
    s.clock = lambda: 0
    s._handle("vision.appearance", {"track_id": 1, "crop": np.zeros((1, 1, 3), np.uint8)}, 0)
    future = client.future
    s._handle("session.forget", {}, 1)
    future.set_result({"color": "blue", "garment": "shirt", "accessory": None})
    s._tick()
    assert not bus.events


def wait_for(predicate):
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_real_ollama_load_response_warms_client(config):
    calls = []

    def transport(payload):
        calls.append(payload)
        return {"message": {"role": "assistant", "content": ""}, "done_reason": "load", "done": True}

    client = OllamaClient(config["llm"], transport)
    client.start()
    try:
        wait_for(lambda: client.warm)
        assert calls[0]["messages"] == []
        assert not client.error
    finally:
        client.stop()


def test_idle_ollama_recovers_after_server_restart(config):
    calls = []

    def transport(payload):
        calls.append(payload)
        if len(calls) == 1:
            raise ConnectionRefusedError("server stopped")
        return {"message": {"content": ""}, "done_reason": "load", "done": True}

    client = OllamaClient(config["llm"] | {"retry_s": 0.01}, transport)
    client.start()
    try:
        wait_for(lambda: bool(client.error))
        wait_for(lambda: client.warm)
        assert not client.error
        assert len(calls) == 2
    finally:
        client.stop()


def test_translation_displaces_queued_description(config):
    client = OllamaClient(config["llm"] | {"max_queue": 2})
    description = client.submit("descriptions", [], {})
    name = client.submit("names", [], {})
    translation = client.submit("translation", [], {})
    assert description.cancelled()
    assert not name.done() and not translation.done()
    assert client.queue.qsize() == 2
    assert client.queue.get_nowait()[3] is translation
    client.stop()


def test_canceled_jobs_release_bounded_queue_capacity(config):
    client = OllamaClient(config["llm"] | {"max_queue": 1})
    obsolete = client.submit("replies", [], {})
    obsolete.cancel()
    latest = client.submit("replies", [], {})
    overflow = client.submit("replies", [], {})
    assert not latest.done()
    assert isinstance(overflow.exception(), RuntimeError)
    assert client.queue.qsize() == 1
    client.stop()


def test_canceling_running_reply_keeps_model_healthy(config):
    entered, release = threading.Event(), threading.Event()

    def transport(payload):
        entered.set()
        release.wait(1)
        return {"message": {"content": '{"options":["Yes","No","Thank you"]}'}}

    client = OllamaClient(config["llm"], transport)
    future = client.submit("replies", [{"role": "user", "content": "hello"}], {})
    client.start()
    try:
        assert entered.wait(1)
        future.cancel()
        release.set()
        wait_for(lambda: client.warm)
        assert not client.error
        assert client.thread.is_alive()
    finally:
        release.set()
        client.stop()


@pytest.mark.parametrize("text", ["I'm tired", "I'm Sam's sister", "I'm Sam’s sister", "This is Sam"])
def test_name_must_be_grounded_in_a_self_introduction(config, text):
    answer = {"is_intro": True, "name": "Sam", "whose": "speaker", "confidence": 0.99}
    assert Names(config["llm"]).propose(caption(text), answer, 0) is None


@pytest.mark.parametrize("text,name", [
    ("Hello, I'm Sam.", "Sam"),
    ("My name is Maya Chen", "Maya Chen"),
    ("Me llamo Ana María.", "Ana María"),
    ("Call me Jean-Luc.", "Jean-Luc"),
])
def test_explicit_introductions_accept_grounded_names(config, text, name):
    answer = {"is_intro": True, "name": name, "whose": "speaker", "confidence": 0.99}
    assert Names(config["llm"]).propose(caption(text), answer, 0)["name"] == name


class PendingClient:
    def __init__(self):
        self.futures = []
        self.warm = True
        self.error = ""

    def submit(self, kind, messages, schema):
        future = Future()
        self.futures.append((kind, future))
        return future

    def cancel_pending(self):
        for _, future in self.futures:
            future.cancel()


def test_forget_received_after_queued_name_answer_cannot_confirm(config, bus):
    service = LLMService(bus, config, client=PendingClient())
    service.clock = lambda: 0
    answer = {"is_intro": True, "name": "Sam", "whose": "speaker", "confidence": 0.99}
    proposal = service.names.propose(caption(), answer, 0)
    service.worker.subscribe("command")
    service.worker.subscribe("session.forget")
    bus.publish("command", {"name": "name.answer", "args": {"proposal_id": proposal["proposal_id"], "accept": True}})
    bus.publish("session.forget", {})
    service.worker.start()
    try:
        wait_for(lambda: not service.names.pending)
    finally:
        service.worker.stop()
    assert not [topic for topic, _ in bus.events if topic in {"name.proposal", "hw.pattern"}]
    assert not service.names.confirmed


def test_expiry_cannot_publish_while_forget_is_waiting(config, bus):
    service = LLMService(bus, config, client=PendingClient())
    service.clock = lambda: 100
    answer = {"is_intro": True, "name": "Sam", "whose": "speaker", "confidence": 0.99}
    service.names.propose(caption(), answer, 0)
    service.worker.subscribe("session.forget")
    bus.publish("session.forget", {})
    service._tick()
    assert not [topic for topic, _ in bus.events if topic == "name.proposal"]
    service.worker.stop()


def test_only_latest_reply_remains_pending(config, bus):
    client = PendingClient()
    service = LLMService(bus, config, client=client)
    service.clock = lambda: 0
    service._handle("caption", caption("Hello"), 0)
    old = client.futures[-1][1]
    service._handle("caption", caption("How are you?") | {"utt_id": "u2"}, 0)
    latest = client.futures[-1][1]
    assert old.cancelled()
    assert len(service.jobs) == 1
    latest.set_result({"options": ["Fine", "Good", "Not bad"]})
    service._tick()
    assert bus.events == [("reply.suggestions", {"options": ["Fine", "Good", "Not bad"]})]


def test_old_introduction_cannot_name_a_reappearing_track(config, bus):
    client = PendingClient()
    service = LLMService(bus, config, client=client)
    service.clock = lambda: 0
    service._handle("caption", caption(), 0)
    old_name = next(future for kind, future in client.futures if kind == "names")
    service._handle("vision.track_lost", {"track_id": 1}, 0)
    service._handle("vision.appearance", {"track_id": 1, "crop": np.zeros((1, 1, 3), np.uint8)}, 0)
    old_name.set_result({"is_intro": True, "name": "Sam", "whose": "speaker", "confidence": 0.99})
    service._tick()
    assert not [topic for topic, _ in bus.events if topic in {"name.proposal", "hw.pattern"}]


def test_llm_health_reports_offline_and_recovery(config, bus):
    client = PendingClient()
    service = LLMService(bus, config, client=client)
    client.warm, client.error = False, "local model offline"
    assert service._health()["ok"] is False
    assert service._health()["detail"] == "local model offline"
    client.warm, client.error = True, ""
    service._error = "old job failed"
    assert service._health() == {"ok": True, "detail": "ready", "metrics": {"warm": True, "pending": 0}}
