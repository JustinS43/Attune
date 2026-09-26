from attune.hardware.touch_router import TouchRouter


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make():
    clock = Clock()
    return TouchRouter(clock), clock


def test_nothing_pending_does_nothing():
    router, _ = make()
    assert router.route("tap") == []
    assert router.route("hold") == []


def test_triple_tap_toggles_pause():
    router, _ = make()
    router.on_alert(
        {"alert_id": "a1", "kind": "smoke", "side": "left", "state": "start"}
    )
    out = router.route("triple")
    assert ("command", {"name": "pause.toggle", "args": {}}) in out
    assert ("touch.action", {"target": "pause", "id": None, "accept": True}) in out
    assert router.current_alert() is not None  # triple does not consume the alert


def test_double_tap_saves_this_person():
    router, clock = make()
    # nobody proposed: the save flow picks who (named speaker, most prominent named face)
    assert router.route("double") == [
        ("touch.action", {"target": "save", "id": None, "accept": True})
    ]
    router.on_proposal(
        {
            "proposal_id": "p1",
            "track_id": 3,
            "name": "Sam",
            "state": "proposed",
            "expires_t": clock.t + 10,
        }
    )
    # with a proposal, the double tap saves that face; no pause, no alert answered
    router.on_alert(
        {"alert_id": "a1", "kind": "doorbell", "side": "right", "state": "start"}
    )
    out = router.route("double")
    assert out == [("touch.action", {"target": "save", "id": "p1", "accept": True})]
    assert router.current_alert() is not None
    # the proposal was used: the next tap goes to the alert, then nothing
    assert router.route("tap")[0][1]["target"] == "alert"
    assert router.route("tap") == []


def test_gesture_map():
    """tap = yes, hold = no, double = save, triple = pause; anything else is ignored."""
    router, clock = make()
    targets = {}
    for g in ("tap", "hold", "double", "triple", "swipe"):
        router.on_proposal(
            {"proposal_id": g, "name": "Sam", "state": "proposed", "expires_t": clock.t + 9}
        )
        out = router.route(g)
        targets[g] = [(e["target"], e["accept"]) for t, e in out if t == "touch.action"]
        router.clear()
    assert targets == {
        "tap": [("name", True)],
        "hold": [("name", False)],
        "double": [("save", True)],
        "triple": [("pause", True)],
        "swipe": [],
    }


def test_alert_beats_name():
    router, clock = make()
    router.on_proposal(
        {
            "proposal_id": "p1",
            "name": "Sam",
            "state": "proposed",
            "expires_t": clock.t + 10,
        }
    )
    clock.t += 1
    router.on_alert(
        {"alert_id": "a1", "kind": "doorbell", "side": "right", "state": "start"}
    )
    assert router.route("tap") == [
        ("touch.action", {"target": "alert", "id": "a1", "accept": True})
    ]
    # the alert is consumed; the next tap confirms the name
    assert router.route("tap") == [
        ("touch.action", {"target": "name", "id": "p1", "accept": True})
    ]
    assert router.route("tap") == []


def test_hold_rejects_name():
    router, clock = make()
    router.on_proposal(
        {
            "proposal_id": "p1",
            "name": "Sam",
            "state": "proposed",
            "expires_t": clock.t + 10,
        }
    )
    assert router.route("hold") == [
        ("touch.action", {"target": "name", "id": "p1", "accept": False})
    ]


def test_expired_and_answered_proposals_are_ignored():
    router, clock = make()
    router.on_proposal(
        {
            "proposal_id": "p1",
            "name": "Sam",
            "state": "proposed",
            "expires_t": clock.t + 10,
        }
    )
    clock.t += 11
    assert router.route("tap") == []
    router.on_proposal(
        {
            "proposal_id": "p2",
            "name": "Ana",
            "state": "proposed",
            "expires_t": clock.t + 10,
        }
    )
    router.on_proposal({"proposal_id": "p2", "state": "confirmed"})
    assert router.route("tap") == []


def test_newest_alert_first_and_cleared_alerts_ignored():
    router, clock = make()
    router.on_alert(
        {"alert_id": "a1", "kind": "smoke", "side": "left", "state": "start"}
    )
    clock.t += 1
    router.on_alert(
        {"alert_id": "a2", "kind": "doorbell", "side": "right", "state": "start"}
    )
    router.on_alert(
        {"alert_id": "a1", "kind": "smoke", "side": "left", "state": "update"}
    )
    assert router.route("tap")[0][1]["id"] == "a2"
    router.on_alert({"alert_id": "a1", "state": "clear"})
    assert router.route("tap") == []


def test_watch_state_is_not_an_alert():
    router, _ = make()
    router.on_alert(
        {"alert_id": "a1", "kind": "smoke", "side": "left", "state": "watch"}
    )
    assert router.route("tap") == []


def test_forget_clears():
    router, clock = make()
    router.on_alert(
        {"alert_id": "a1", "kind": "smoke", "side": "left", "state": "start"}
    )
    router.on_proposal(
        {
            "proposal_id": "p1",
            "name": "Sam",
            "state": "proposed",
            "expires_t": clock.t + 10,
        }
    )
    router.clear()
    assert router.route("tap") == []
