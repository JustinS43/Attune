"""V-23 / P-35: the enrollment station's messages reach only the page that started the save.

Also the station's contract additions and its consent in the save flow.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import time

import pytest
from attune.core import contracts as C
from attune.core.bus import Bus
from attune.core.save_flow import SaveFlow
from attune.server.ws import Client, Hub

from .conftest import CONFIG, Recorder, recv, recv_type, wait_for
from .test_save_flow import Clock, double_tap, face, scene
from .test_ws_hub import page

JPEG = b"\xff\xd8\xff\xe0 fake jpeg bytes \xff\xd9"


def state(sid="station-1", phase="face", cid=None, **extra):
    return {"session_id": sid, "client_id": cid, "phase": phase, "name": "Sam", **extra}


def preview(sid="station-1", cid=None):
    return {
        "session_id": sid,
        "client_id": cid,
        "jpeg": JPEG,
        "width": 360,
        "height": 480,
        "face": [0.3, 0.2, 0.4, 0.5],
        "ok": True,
        "hint": "",
    }


def client_ids(hub):
    return {c.role: cid for cid, c in hub.clients.items()}


def recv_types(ws, types, timeout=10.0):
    """The first message of each of `types`, in whatever order they arrive."""
    got, end = {}, time.monotonic() + timeout
    while len(got) < len(types):
        left = end - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"got {sorted(got)} of {sorted(types)}")
        msg = recv(ws, left)
        if isinstance(msg, dict) and msg.get("type") in types:
            got.setdefault(msg["type"], msg)
    return got


# ------------------------------------------------------------------ contracts
def test_station_contract_names_and_defaults():
    assert "enroll.station" in C.COMMAND_NAMES
    for topic in (C.ENROLL_STATE, C.ENROLL_PREVIEW, C.ENROLL_LEVEL, C.ENROLL_MISMATCH):
        assert topic in C.TOPICS and topic in C.WS_STATION
    assert C.WS_STATION[C.ENROLL_PREVIEW] == "enroll_preview"
    assert C.ENROLL_STATION_ACTIONS == (
        "start",
        "retry",
        "new_person",
        "skip_voice",
        "cancel",
    )
    # old producers keep working: the new fields have defaults
    assert C.EnrollResult(None, "face", False).source == "glasses"
    assert C.EnrollProgress(3, "face", 0.5).session_id is None
    assert C.VoiceHarvest("p", 0.0, 1.5).talkers == 1
    names = {f.name for f in dataclasses.fields(C.EnrollState)}
    assert {"session_id", "phase", "name", "sentence", "need_s", "reason"} <= names


def test_station_code_uses_the_contract_phases():
    from attune.station import session as S

    phases = (S.OPENING, S.FACE, S.MISMATCH, S.FACE_FAILED, S.SAVING, S.VOICE)
    phases += (S.VOICE_FAILED, S.DONE, S.CANCELLED, S.FALLBACK)
    assert phases == C.ENROLL_PHASES
    assert S.ACTIONS == C.ENROLL_STATION_ACTIONS


# ------------------------------------------------------------------ hub routing
def test_station_command_carries_the_page_id(hub_env):
    with page(hub_env.client, "phone") as (ws, _):
        ws.send_json(
            {
                "type": "command",
                "name": "enroll.station",
                "args": {"action": "start", "client_id": 999},
            }
        )
        assert wait_for(lambda: hub_env.rec[C.COMMAND])
        args = hub_env.rec[C.COMMAND][-1]["args"]
        cid = client_ids(hub_env.hub)["phone"]
        assert args["client_id"] == cid  # set by the hub; a page can't pick another page


def test_preview_and_state_only_to_the_page_that_started_it(hub_env):
    with (
        page(hub_env.client, "phone") as (phone, _),
        page(hub_env.client, "phone") as (other, _),
    ):
        mine = min(hub_env.hub.clients)  # the first phone
        hub_env.bus.publish(C.ENROLL_STATE, state(cid=mine))
        hub_env.bus.publish(C.ENROLL_PREVIEW, preview(cid=mine))
        hub_env.bus.publish(
            C.ENROLL_LEVEL,
            {
                "session_id": "station-1",
                "client_id": mine,
                "level": 0.5,
                "db": -30.0,
                "hint": "",
            },
        )
        # the hub sends queued messages before the newest preview, so on a busy laptop the
        # level can come before the preview: take the three in whatever order they come
        got = recv_types(phone, ("enroll_state", "enroll_preview", "enroll_level"))
        state_msg = got["enroll_state"]
        assert state_msg["phase"] == "face" and "client_id" not in state_msg
        pv = got["enroll_preview"]
        assert base64.b64decode(pv["jpeg_b64"]) == JPEG and "jpeg" not in pv
        assert got["enroll_level"]["level"] == 0.5
        # the other phone gets none of it: a caption sent afterwards arrives first
        hub_env.bus.publish(
            C.CAPTION,
            C.Caption("u1", C.Speaker("face", 1, None, "Maya"), "hi", True, "en", []),
        )
        seen = []
        recv_type(other, "caption", seen=seen)
        assert not [m for m in seen if isinstance(m, dict) and m["type"].startswith("enroll_")]


def test_a_slow_phone_only_gets_the_newest_preview():
    client = Client(None, 1)
    for i in range(5):
        client.push_preview({"session_id": "s", "jpeg_b64": str(i)})
    assert json.loads(client.preview)["jpeg_b64"] == "4"  # older previews were dropped
    assert not client.queue  # and never queued behind captions


def test_a_reconnecting_phone_takes_the_save_over(hub_env):
    hub = hub_env.hub
    with page(hub_env.client, "phone") as (phone, _):
        first = next(iter(hub.clients))
        hub_env.bus.publish(C.ENROLL_STATE, state(cid=first, phase="voice"))
        recv_type(phone, "enroll_state")
    assert wait_for(lambda: first not in hub.clients)
    with page(hub_env.client, "phone") as (again, _):
        resumed = recv_type(again, "enroll_state")
        assert resumed["phase"] == "voice"  # the screen it was on
        hub_env.bus.publish(
            C.ENROLL_LEVEL,
            {
                "session_id": "station-1",
                "client_id": first,
                "level": 0.2,
                "db": -48.0,
                "hint": "speak up",
            },
        )
        assert recv_type(again, "enroll_level")["hint"] == "speak up"
        hub_env.bus.publish(C.ENROLL_STATE, state(cid=first, phase="done"))
        assert recv_type(again, "enroll_state")["phase"] == "done"
    assert hub.station_session is None and hub.station_state is None


def test_welcome_says_where_people_are_saved(tmp_path):
    hub = Hub(Bus(), {**CONFIG, "enroll": {"source": "station", "sentence": "Read me."}})
    assert hub.welcome_config["enroll"] == {"source": "station", "sentence": "Read me."}
    assert "enroll" not in Hub(Bus(), CONFIG).welcome_config  # older configs: unchanged


def test_station_consent_ends_the_waiting_request_on_the_hub(hub_env):
    hub = hub_env.hub
    hub.save_pending = {"request_id": "save-1", "expires_t": 1e12}
    hub_env.bus.publish(
        C.COMMAND,
        {"name": "enroll.station", "args": {"action": "start", "request_id": "save-1"}},
    )
    assert wait_for(lambda: hub.save_pending is None)


# ------------------------------------------------------------------ save flow
@pytest.fixture
def station_flow():
    bus, clock = Bus(), Clock()
    rec = Recorder(bus, C.SAVE_REQUEST, C.SAVE_CANCEL, C.COMMAND, C.HW_PATTERN)
    flow = SaveFlow(
        bus, {"save": {"consent_timeout_s": 60}, "enroll": {"source": "station"}}, clock
    )
    flow.connect()
    yield bus, clock, rec, flow
    flow.stop()


def test_station_consent_answers_the_save_request(station_flow):
    bus, _clock, rec, flow = station_flow
    bus.publish(C.SCENE, scene(face(2, "Sam", "named")))
    double_tap(bus)
    rid = rec[C.SAVE_REQUEST][-1]["request_id"]
    bus.publish(
        C.COMMAND,
        {
            "name": "enroll.station",
            "args": {
                "action": "start",
                "track_id": 2,
                "name": "Sam",
                "consent": True,
                "consent_t": 1.0,
                "request_id": rid,
            },
        },
    )
    assert flow.active is None and 2 in flow.enrolling
    assert not rec[C.SAVE_CANCEL]


def test_walking_to_the_laptop_does_not_cancel_the_request(station_flow):
    bus, clock, rec, flow = station_flow
    bus.publish(C.SCENE, scene(face(2, "Sam", "named")))
    double_tap(bus)
    bus.publish(C.VISION_TRACK_LOST, {"track_id": 2, "t": 0, "side": "left"})
    assert flow.active is not None and not rec[C.SAVE_CANCEL]
    clock.t += 61
    flow.check_timeout()
    assert rec[C.SAVE_CANCEL][-1]["reason"] == "timeout"
