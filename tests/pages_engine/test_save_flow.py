"""Save a person with a double tap (P-29): who is saved, consent, cancel, timeout, relays."""

from __future__ import annotations

import pytest
from attune.core import contracts as C
from attune.core.bus import Bus
from attune.core.save_flow import SaveFlow
from attune.hardware.touch_router import TouchRouter

from .conftest import Recorder, recv_type
from .test_ws_hub import page


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def face(tid, label, status, box=(860, 440, 200, 200)):
    return C.FaceState(tid, list(box), label, status, 0.0, False, False)


def scene(*faces):
    return C.Scene(1, 0.0, list(faces), [], False)


@pytest.fixture
def env():
    bus, clock = Bus(), Clock()
    rec = Recorder(bus, C.SAVE_REQUEST, C.SAVE_CANCEL, C.COMMAND, C.HW_PATTERN)
    flow = SaveFlow(bus, {"save": {"consent_timeout_s": 60}}, clock)
    flow.connect()
    yield bus, clock, rec, flow
    flow.stop()


def double_tap(bus, proposal_id=None):
    bus.publish(C.TOUCH_ACTION, {"target": "save", "id": proposal_id, "accept": True})


# ---------------------------------------------------------------- who is saved


def test_proposal_face_first_and_its_name_is_confirmed(env):
    bus, clock, rec, _ = env
    bus.publish(
        C.SCENE,
        scene(
            face(1, "Maya", "named", (100, 100, 400, 400)),
            face(2, "Person in red hat", "proposed"),
        ),
    )
    bus.publish(
        C.CAPTION,
        C.Caption("u1", C.Speaker("face", 1, None, "Maya"), "Hi", True, "en", []),
    )
    bus.publish(
        C.NAME_PROPOSAL,
        {
            "proposal_id": "p1",
            "track_id": 2,
            "name": "Sam",
            "state": "proposed",
            "expires_t": clock.t + 8,
        },
    )
    double_tap(bus, "p1")
    req = rec[C.SAVE_REQUEST][-1]
    assert (req["track_id"], req["name"], req["proposal_id"]) == (2, "Sam", "p1")
    assert req["expires_t"] == pytest.approx(clock.t + 60)
    # saving someone also confirms their name for the session, like a tap
    assert {
        "name": "name.answer",
        "args": {"proposal_id": "p1", "accept": True},
    } in rec[C.COMMAND]
    assert rec[C.HW_PATTERN][-1] == {"name": "OK", "side": "B"}


def test_just_confirmed_proposal_still_counts(env):
    """Y then Y (a tap, then a quick second tap on the keyboard) saves the name just confirmed."""
    bus, clock, rec, _ = env
    bus.publish(
        C.SCENE,
        scene(face(5, "Sam", "named"), face(6, "Ana", "named", (0, 0, 600, 600))),
    )
    bus.publish(
        C.NAME_PROPOSAL,
        {"proposal_id": "p9", "track_id": 5, "name": "Sam", "state": "confirmed"},
    )
    clock.t += 2
    bus.publish(C.COMMAND, {"name": "save.start", "args": {}})
    assert rec[C.SAVE_REQUEST][-1]["track_id"] == 5
    assert not [
        c for c in rec[C.COMMAND] if c["name"] == "name.answer"
    ]  # already confirmed


def test_named_speaker_before_the_biggest_face(env):
    bus, _clock, rec, _ = env
    bus.publish(
        C.SCENE,
        scene(
            face(1, "Maya", "named", (100, 100, 500, 500)),
            face(2, "Leo", "named", (1500, 700, 120, 120)),
        ),
    )
    bus.publish(
        C.CAPTION,
        C.Caption("u1", C.Speaker("face", 2, None, "Leo"), "Hey", True, "en", []),
    )
    double_tap(bus)
    assert rec[C.SAVE_REQUEST][-1]["name"] == "Leo"


def test_most_prominent_named_face_when_nobody_spoke(env):
    bus, clock, rec, flow = env
    bus.publish(
        C.SCENE,
        scene(
            face(1, "Maya", "named", (0, 0, 260, 260)),  # corner
            face(2, "Leo", "named", (840, 420, 240, 240)),  # about as big, centred
            face(
                3, "Person in blue top", "unknown", (700, 300, 600, 600)
            ),  # biggest, no name
            face(4, "Ana", "enrolled", (820, 400, 400, 400)),  # already saved
        ),
    )
    # a speaker too long ago does not count
    bus.publish(
        C.CAPTION,
        C.Caption("u1", C.Speaker("face", 1, None, "Maya"), "Hi", True, "en", []),
    )
    clock.t += flow.speaker_s + 1
    bus.publish(
        C.SCENE,
        scene(
            face(1, "Maya", "named", (0, 0, 260, 260)),
            face(2, "Leo", "named", (840, 420, 240, 240)),
        ),
    )
    double_tap(bus)
    assert rec[C.SAVE_REQUEST][-1]["name"] == "Leo"


def test_nobody_named_gives_a_hint(env):
    bus, clock, rec, _ = env
    bus.publish(C.SCENE, scene(face(3, "Person in blue top", "unknown")))
    double_tap(bus)
    assert not rec[C.SAVE_REQUEST]
    assert rec[C.SAVE_CANCEL][-1] == {
        "request_id": None,
        "reason": "no_name",
        "track_id": None,
        "name": "",
    }
    assert rec[C.HW_PATTERN][-1]["name"] == "NO"
    bus.publish(C.SCENE, scene(face(4, "Ana", "enrolled")))
    double_tap(bus)
    assert rec[C.SAVE_CANCEL][-1]["reason"] == "already_saved"
    assert rec[C.SAVE_CANCEL][-1]["name"] == "Ana"
    # an old scene is not "in view"
    bus.publish(C.SCENE, scene(face(5, "Leo", "named")))
    clock.t += 5
    double_tap(bus)
    assert rec[C.SAVE_CANCEL][-1]["reason"] == "no_name"


def test_router_double_tap_carries_the_proposal_to_the_flow(env):
    bus, clock, rec, _ = env
    router = TouchRouter(clock)
    bus.subscribe(C.NAME_PROPOSAL, router.on_proposal)
    bus.publish(
        C.SCENE,
        scene(
            face(7, "Person in red hat", "proposed"),
            face(8, "Maya", "named", (0, 0, 900, 900)),
        ),
    )
    bus.publish(
        C.NAME_PROPOSAL,
        {
            "proposal_id": "p7",
            "track_id": 7,
            "name": "Andre",
            "state": "proposed",
            "expires_t": clock.t + 8,
        },
    )
    for topic, event in router.route("double"):
        bus.publish(topic, event)
    assert rec[C.SAVE_REQUEST][-1]["name"] == "Andre"


# ---------------------------------------------------------------- consent and endings


def test_consent_path_to_enroll_start(env):
    bus, clock, rec, flow = env
    bus.publish(C.SCENE, scene(face(2, "Sam", "named")))
    double_tap(bus)
    req = rec[C.SAVE_REQUEST][-1]
    # a double tap again shows the same request, it does not start a second one
    double_tap(bus)
    assert rec[C.SAVE_REQUEST][-1]["request_id"] == req["request_id"]
    # the flow itself never enrolls: only the page's enroll.start (the person's consent) does
    assert not [c for c in rec[C.COMMAND] if c["name"] == "enroll.start"]
    bus.publish(
        C.COMMAND,
        {
            "name": "enroll.start",
            "args": {
                "track_id": 2,
                "name": "Sam",
                "consent": True,
                "consent_t": 1.0,
                "request_id": req["request_id"],
            },
        },
    )
    assert flow.active is None
    clock.t += 120
    flow.check_timeout()
    assert not rec[C.SAVE_CANCEL]  # answered: nothing times out
    # while the enrollment runs, another double tap does not ask again
    double_tap(bus)
    assert len(rec[C.SAVE_REQUEST]) == 2
    bus.publish(C.ENROLL_RESULT, C.EnrollResult("sam-1", "voice", True, "", 2))
    bus.publish(C.SCENE, scene(face(2, "Sam", "named")))
    double_tap(bus)
    assert len(rec[C.SAVE_REQUEST]) == 3


def test_consent_without_the_tick_does_not_answer(env):
    bus, _clock, rec, flow = env
    bus.publish(C.SCENE, scene(face(2, "Sam", "named")))
    double_tap(bus)
    rid = rec[C.SAVE_REQUEST][-1]["request_id"]
    bus.publish(
        C.COMMAND,
        {
            "name": "enroll.start",
            "args": {"track_id": 2, "name": "Sam", "consent": False, "request_id": rid},
        },
    )
    assert flow.active is not None


@pytest.mark.parametrize(
    "ending, reason",
    [
        (
            lambda bus, rid: bus.publish(
                C.COMMAND, {"name": "save.cancel", "args": {"request_id": rid}}
            ),
            "declined",
        ),
        (
            lambda bus, rid: bus.publish(
                C.VISION_TRACK_LOST, {"track_id": 2, "t": 0, "side": "left"}
            ),
            "lost",
        ),
        (lambda bus, rid: bus.publish(C.PAUSED, {"paused": True}), "cancelled"),
        (lambda bus, rid: bus.publish(C.SESSION_FORGET, {}), "cancelled"),
    ],
)
def test_request_endings(env, ending, reason):
    bus, _clock, rec, flow = env
    bus.publish(C.SCENE, scene(face(2, "Sam", "named")))
    double_tap(bus)
    rid = rec[C.SAVE_REQUEST][-1]["request_id"]
    ending(bus, rid)
    assert rec[C.SAVE_CANCEL][-1] == {
        "request_id": rid,
        "reason": reason,
        "track_id": 2,
        "name": "Sam",
    }
    assert flow.active is None


def test_timeout_after_a_minute(env):
    bus, clock, rec, flow = env
    bus.publish(C.SCENE, scene(face(2, "Sam", "named")))
    double_tap(bus)
    clock.t += 59
    flow.check_timeout()
    assert not rec[C.SAVE_CANCEL]
    clock.t += 1.5
    flow.check_timeout()
    assert rec[C.SAVE_CANCEL][-1]["reason"] == "timeout"


def test_someone_else_replaces_the_request(env):
    bus, _clock, rec, _ = env
    bus.publish(
        C.SCENE, scene(face(2, "Sam", "named"), face(3, "Leo", "named", (0, 0, 50, 50)))
    )
    bus.publish(C.COMMAND, {"name": "save.start", "args": {"track_id": 2}})
    first = rec[C.SAVE_REQUEST][-1]["request_id"]
    bus.publish(C.COMMAND, {"name": "save.start", "args": {"track_id": 3}})
    assert rec[C.SAVE_CANCEL][-1] == {
        "request_id": first,
        "reason": "replaced",
        "track_id": 2,
        "name": "Sam",
    }
    assert rec[C.SAVE_REQUEST][-1]["name"] == "Leo"


# ---------------------------------------------------------------- hub relays


def test_hub_relays_save_messages_and_progress_to_every_page(hub_env):
    bus = hub_env.bus
    with (
        page(hub_env.client, "lens") as (lens, _),
        page(hub_env.client, "phone") as (phone, _),
    ):
        req = {
            "request_id": "save-1",
            "track_id": 2,
            "name": "Sam",
            "t": 1.0,
            "expires_t": 1e9,
            "person_id": None,
            "proposal_id": None,
        }
        bus.publish(C.SAVE_REQUEST, req)
        for ws in (lens, phone):
            assert recv_type(ws, "save_request")["name"] == "Sam"
        # a page that opens while the request waits still gets it
        with page(hub_env.client, "console") as (console, _):
            assert recv_type(console, "save_request")["request_id"] == "save-1"
        bus.publish(
            C.ENROLL_PROGRESS,
            {
                "track_id": 2,
                "person_id": None,
                "part": "face",
                "fraction": 0.5,
                "hint": "more light",
            },
        )
        bus.publish(C.ENROLL_RESULT, C.EnrollResult("sam-1", "face", True, "", 2))
        for ws in (lens, phone):
            prog = recv_type(ws, "enroll_progress")
            assert (prog["part"], prog["fraction"], prog["hint"]) == (
                "face",
                0.5,
                "more light",
            )
            assert recv_type(ws, "enroll_result")["ok"] is True
        bus.publish(
            C.SAVE_CANCEL,
            {
                "request_id": "save-1",
                "reason": "declined",
                "track_id": 2,
                "name": "Sam",
            },
        )
        assert recv_type(lens, "save_cancel")["reason"] == "declined"
        assert hub_env.hub.save_pending is None


def test_enroll_start_from_a_page_answers_the_pending_request(hub_env):
    bus = hub_env.bus
    bus.publish(
        C.SAVE_REQUEST,
        {
            "request_id": "save-2",
            "track_id": 4,
            "name": "Sam",
            "t": 1.0,
            "expires_t": 1e9,
        },
    )
    with page(hub_env.client, "phone") as (phone, _):
        assert recv_type(phone, "save_request")["request_id"] == "save-2"
        phone.send_json(
            {
                "type": "command",
                "name": "enroll.start",
                "args": {
                    "track_id": 4,
                    "name": "Sam",
                    "consent": True,
                    "consent_t": 1.0,
                    "request_id": "save-2",
                },
            }
        )
        from .conftest import wait_for

        assert wait_for(lambda: hub_env.hub.save_pending is None)
        assert hub_env.rec[C.COMMAND][-1]["args"]["request_id"] == "save-2"


def test_save_contract_names():
    assert {"save.start", "save.cancel"} <= C.COMMAND_NAMES
    assert {C.SAVE_REQUEST, C.SAVE_CANCEL, C.ENROLL_PROGRESS} <= C.TOPICS
    for msg in (
        C.WS_SAVE_REQUEST,
        C.WS_SAVE_CANCEL,
        C.WS_ENROLL_PROGRESS,
        C.WS_ENROLL_RESULT,
    ):
        assert C.WS_AUDIENCE[msg] == {"lens", "console", "phone"}
    assert C.GESTURES == ("tap", "hold", "double", "triple")
    assert "save" in C.TOUCH_TARGETS
    assert C.EnrollProgress(3, "face", 0.25).hint == ""
    assert C.SaveCancel(None, "no_name").track_id is None
