"""P-44: a page that (re)connects mid-alarm gets the alerts still sounding."""

from __future__ import annotations

from attune.core import contracts as C

from .conftest import recv, wait_for
from .test_caption_replay import connected


def alert(alert_id, kind, state, side="left"):
    return C.Alert(alert_id, kind, side, 0.9, state)


def test_lens_that_reloads_mid_alarm_shows_it_again(hub_env):
    bus, hub = hub_env.bus, hub_env.hub
    bus.publish(C.ALERT, alert("a1", "smoke", "start"))
    bus.publish(C.ALERT, alert("a1", "smoke", "update", side="right"))
    bus.publish(C.ALERT, alert("a2", "doorbell", "start"))
    bus.publish(C.ALERT, alert("a2", "doorbell", "acknowledged"))
    bus.publish(C.ALERT, alert("a3", "co", "start"))
    bus.publish(C.ALERT, alert("a3", "co", "clear"))
    assert wait_for(lambda: list(hub.active_alerts) == ["a1"])
    with connected(hub_env.client, "lens") as ws:
        assert recv(ws)["type"] == "welcome"
        got = recv(ws)
        assert got["type"] == "alert" and got["alert_id"] == "a1"
        # the latest message, not the first
        assert got["side"] == "right" and got["state"] == "update"
        # nothing acknowledged or cleared follows: the next alert is a live one
        bus.publish(C.ALERT, alert("a4", "doorbell", "start"))
        assert recv(ws)["alert_id"] == "a4"


def test_no_alert_replayed_once_it_clears(hub_env):
    bus, hub = hub_env.bus, hub_env.hub
    bus.publish(C.ALERT, alert("a1", "smoke", "start"))
    assert wait_for(lambda: "a1" in hub.active_alerts)
    bus.publish(C.ALERT, alert("a1", "smoke", "clear"))
    assert wait_for(lambda: not hub.active_alerts)
    with connected(hub_env.client, "phone") as ws:
        assert recv(ws)["type"] == "welcome"
        bus.publish(C.ALERT, alert("a2", "doorbell", "start"))
        msg = recv(ws)
        while msg["type"] != "alert":
            msg = recv(ws)
        assert msg["alert_id"] == "a2"
