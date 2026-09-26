"""A-23: translations never wait behind slower local-model jobs; slow answers are skipped."""

import json
import threading
import time
from concurrent.futures import Future

import pytest
from attune.llm.client import NUM_PREDICT, OllamaClient
from attune.llm.service import LLMService


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


ANSWERS = {
    "translation": {"text_en": "Where is the station?"},
    "replies": {"options": ["Yes", "No", "Maybe"]},
    "descriptions": {"color": "blue", "garment": "shirt", "accessory": None},
    "names": {"is_intro": False, "name": "", "whose": "none", "confidence": 0},
}


class SlowModel:
    """A fake Ollama: the first `slow` job runs until the client stops it (the socket closes)."""

    def __init__(self, slow: str | None = None):
        self.slow = slow
        self.calls: list[str] = []
        self.entered = threading.Event()
        self.client: OllamaClient | None = None

    def __call__(self, payload):
        if not payload["messages"]:
            return {"message": {"content": ""}, "done_reason": "load"}
        kind = payload["_kind"]
        self.calls.append(kind)
        if kind == self.slow:
            self.slow = None
            self.entered.set()
            deadline = time.monotonic() + 2
            while not self.client._stopped and time.monotonic() < deadline:
                time.sleep(0.002)
            raise ConnectionResetError("closed by the client")
        return {"message": {"content": json.dumps(ANSWERS[kind])}}


def started(config, model, **overrides):
    client = OllamaClient(config["llm"] | overrides, model)
    model.client = client
    client.warm = True  # skip the warm-up
    client.thread = threading.Thread(target=client._run, daemon=True)
    client.thread.start()
    return client


def test_translation_stops_a_running_reply_which_runs_again_after_it(config):
    model = SlowModel(slow="replies")
    client = started(config, model)
    try:
        reply = client.submit("replies", [{"role": "user", "content": "hi"}], {})
        assert model.entered.wait(1)
        translation = client.submit(
            "translation", [{"role": "user", "content": "hola"}], {}
        )
        assert translation.result(timeout=1) == ANSWERS["translation"]
        assert reply.result(timeout=1) == ANSWERS["replies"]
        assert model.calls == ["replies", "translation", "replies"]
        assert client.error == "" and client.warm
    finally:
        client.stop()


def test_a_translation_does_not_stop_a_running_name_check(config):
    release = threading.Event()
    calls = []

    def transport(payload):
        calls.append(payload["_kind"])
        if payload["_kind"] == "names":
            release.wait(1)
        return {"message": {"content": json.dumps(ANSWERS[payload["_kind"]])}}

    client = OllamaClient(config["llm"], transport)
    client.warm = True
    name = client.submit("names", [{"role": "user", "content": "I'm Sam"}], {})
    client.thread = threading.Thread(target=client._run, daemon=True)
    client.thread.start()
    try:
        wait_for(lambda: calls == ["names"])
        translation = client.submit(
            "translation", [{"role": "user", "content": "hola"}], {}
        )
        assert not client._stopped
        release.set()
        assert name.result(timeout=1) == ANSWERS["names"]
        assert translation.result(timeout=1) == ANSWERS["translation"]
    finally:
        release.set()
        client.stop()


def test_cancelling_the_running_reply_stops_it_and_frees_the_model(config):
    model = SlowModel(slow="replies")
    client = started(config, model)
    try:
        old = client.submit("replies", [{"role": "user", "content": "hi"}], {})
        assert model.entered.wait(1)
        old.cancel()  # a newer final made it obsolete
        new = client.submit("replies", [{"role": "user", "content": "hi there"}], {})
        assert new.result(timeout=1) == ANSWERS["replies"]
        assert model.calls == ["replies", "replies"]
        assert client.error == ""
    finally:
        client.stop()


def test_each_kind_has_its_own_answer_cap_and_timeout(config):
    seen = {}

    def transport(payload):
        seen[payload["_kind"]] = payload
        return {"message": {"content": json.dumps(ANSWERS[payload["_kind"]])}}

    cfg = config["llm"] | {
        "timeouts": {"translation": 4.0, "replies": 6.0},
        "timeout_s": 1.5,
    }
    cfg["num_predict"] = {"replies": 80}
    client = OllamaClient(cfg, transport)
    client.warm = True
    for kind in ("translation", "replies", "descriptions"):
        client.submit(kind, [{"role": "user", "content": "x"}], {})
    client.thread = threading.Thread(target=client._run, daemon=True)
    client.thread.start()
    try:
        wait_for(lambda: len(seen) == 3)
    finally:
        client.stop()
    assert seen["translation"]["options"]["num_predict"] == NUM_PREDICT["translation"]
    assert seen["replies"]["options"]["num_predict"] == 80
    assert client._timeout(seen["translation"]) == 4.0
    assert client._timeout(seen["replies"]) == 6.0
    assert client._timeout(seen["descriptions"]) == 1.5
    assert client._timeout({"messages": []}) == cfg.get("load_timeout_s", 60.0)
    assert client.stats["replies"]["count"] == 1


def test_a_slow_answer_is_skipped_without_marking_the_model_offline(config):
    def transport(payload):
        if payload["_kind"] == "descriptions":
            raise TimeoutError("timed out")
        return {"message": {"content": json.dumps(ANSWERS[payload["_kind"]])}}

    client = OllamaClient(config["llm"], transport)
    client.warm = True
    slow = client.submit("descriptions", [{"role": "user", "content": "x"}], {})
    client.thread = threading.Thread(target=client._run, daemon=True)
    client.thread.start()
    try:
        with pytest.raises(TimeoutError):
            slow.result(timeout=1)
        assert client.warm and client.error == ""
        after = client.submit("translation", [{"role": "user", "content": "hola"}], {})
        assert after.result(timeout=1) == ANSWERS["translation"]
        assert client.stats["descriptions"]["failures"] == 1
    finally:
        client.stop()


class Pending:
    def __init__(self):
        self.jobs = []
        self.warm, self.error = True, ""

    def submit(self, kind, messages, schema):
        future = Future()
        self.jobs.append((kind, messages, future))
        return future

    def cancel_pending(self):
        pass


def final(text, utt, lang="en"):
    return {
        "utt_id": utt,
        "text": text,
        "final": True,
        "lang": lang,
        "speaker": {"kind": "someone", "track_id": None, "person_id": None},
    }


def test_replies_are_asked_for_once_the_talk_pauses(config, bus):
    config["llm"] = config["llm"] | {"reply_delay_s": 1.0}
    client = Pending()
    service = LLMService(bus, config, client=client)
    now = [0.0]
    service.clock = lambda: now[0]
    service._handle("caption", final("Hi there.", "u1"), 0)
    now[0] = 0.5
    service._handle("caption", final("Are you coming tonight?", "u2"), 0)
    service._tick()
    assert [k for k, _, _ in client.jobs] == []  # still talking
    now[0] = 1.6
    service._tick()
    assert [k for k, _, _ in client.jobs] == ["replies"]
    assert "Are you coming tonight?" in client.jobs[0][1][-1]["content"]
    service._tick()
    assert len(client.jobs) == 1  # asked once


def test_a_translation_is_queued_at_once_even_while_replies_wait(config, bus):
    config["llm"] = config["llm"] | {"reply_delay_s": 1.0}
    client = Pending()
    service = LLMService(bus, config, client=client)
    service.clock = lambda: 0.0
    service._handle("caption", final("¿Dónde está la estación?", "u1", "es"), 0)
    assert [k for k, _, _ in client.jobs] == ["translation"]
