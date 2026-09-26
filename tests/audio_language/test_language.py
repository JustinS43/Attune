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
