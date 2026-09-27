"""A-22: a page that (re)connects gets the captions it missed."""

from __future__ import annotations

import contextlib

from attune.core import contracts as C

from .conftest import recv, wait_for


@contextlib.contextmanager
def connected(client, role):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "role": role, "frames": False})
        yield ws


def caption(utt, text, final=True):
    speaker = C.Speaker("someone", None, None, "Someone", "none")
    return C.Caption(utt, speaker, text, final, "es", [(text, 1.0, 1.4)])


def until_quiet(ws, stop_type):
    """Messages up to and including the first of `stop_type`."""
    out = []
    while True:
        msg = recv(ws)
        out.append(msg)
        if msg["type"] == stop_type:
            return out


def test_phone_that_reconnects_gets_what_was_said_meanwhile(hub_env):
    bus = hub_env.bus
    bus.publish(C.CAPTION, caption("u1", "Hola"))
    bus.publish(C.CAPTION, caption("u2", "¿Qué tal?"))
    bus.publish(C.CAPTION_TRANSLATION, C.CaptionTranslation("u2", "es", "How are you?"))
    bus.publish(C.CAPTION, caption("u3.1", "gone"))
    bus.publish(C.CAPTION_RETRACT, C.CaptionRetract("u3.1"))
    assert wait_for(lambda: list(hub_env.hub.captions) == ["u1", "u2"])
    with connected(hub_env.client, "phone") as ws:
        assert recv(ws)["type"] == "welcome"
        got = [recv(ws), recv(ws)]
        assert [m["type"] for m in got] == ["caption", "caption"]
        assert [m["utt_id"] for m in got] == ["u1", "u2"]
        assert got[1]["translation"] == "How are you?"
        # live captions keep coming after the replay, in order
        bus.publish(C.CAPTION, caption("u4", "Adiós"))
        rest = until_quiet(ws, "caption")
        assert rest[-1]["utt_id"] == "u4"


def test_lens_gets_only_what_is_still_on_screen(hub_env):
    hub = hub_env.hub
    now = [100.0]
    hub.clock = lambda: now[0]
    hub_env.bus.publish(C.CAPTION, caption("old", "Hace rato"))
    assert wait_for(lambda: "old" in hub.captions)
    now[0] = 120.0
    hub_env.bus.publish(C.CAPTION, caption("new", "Ahora", final=False))
    assert wait_for(lambda: "new" in hub.captions)
    with connected(hub_env.client, "lens") as ws:
        assert recv(ws)["type"] == "welcome"
        first = recv(ws)
        assert first["type"] == "caption" and first["utt_id"] == "new"
        assert first["final"] is False
        # nothing older follows: the next message is a live one
        hub_env.bus.publish(C.CAPTION, caption("next", "Y luego"))
        assert recv(ws)["utt_id"] == "next"


def test_forgotten_captions_are_not_replayed(hub_env):
    hub_env.bus.publish(C.CAPTION, caption("u1", "Me llamo Ana"))
    assert wait_for(lambda: "u1" in hub_env.hub.captions)
    hub_env.bus.publish(C.SESSION_FORGET, {})
    assert wait_for(lambda: not hub_env.hub.captions)
    with connected(hub_env.client, "phone") as ws:
        assert recv(ws)["type"] == "welcome"
        hub_env.bus.publish(C.CAPTION, caption("u2", "Hola"))
        msgs = until_quiet(ws, "caption")
        assert [m["utt_id"] for m in msgs if m["type"] == "caption"] == ["u2"]
