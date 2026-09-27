"""Cloud captions in the engine (P-48): cloud.state to every page, cloud.set to the bus."""

from __future__ import annotations

from attune import main as M
from attune.core import contracts as C

from .conftest import recv_type, wait_for
from .test_ws_hub import caption_event, page


def cloud_state(enabled=True, state="on", reason="", latency_ms=420):
    return C.CloudState(
        enabled=enabled,
        state=state,
        reason=reason,
        latency_ms=latency_ms,
        provider="google",
        model="latest_long",
        language="en-US",
        languages=["en-US", "es-US"],
        credentials=True,
    )


def test_cloud_state_goes_to_every_role(hub_env):
    with (
        page(hub_env.client, "lens") as (lens, _),
        page(hub_env.client, "phone") as (phone, _),
        page(hub_env.client, "console") as (console, _),
    ):
        hub_env.bus.publish(C.CLOUD_STATE, cloud_state())
        for ws in (lens, phone, console):
            msg = recv_type(ws, "cloud")
            assert msg["enabled"] is True and msg["state"] == "on"
            assert msg["latency_ms"] == 420 and msg["languages"] == ["en-US", "es-US"]
            assert msg["credentials"] is True
            assert set(msg) - {"type", "seq"} == {
                "enabled",
                "state",
                "reason",
                "latency_ms",
                "provider",
                "model",
                "language",
                "languages",
                "credentials",
            }


def test_latest_cloud_state_follows_welcome(hub_env):
    hub_env.bus.publish(C.CLOUD_STATE, cloud_state(state="connecting", latency_ms=None))
    hub_env.bus.publish(C.CLOUD_STATE, cloud_state(state="fallback", reason="network"))
    assert wait_for(lambda: (hub_env.hub._cloud or {}).get("state") == "fallback")
    for role in ("lens", "phone", "console"):
        with page(hub_env.client, role) as (ws, welcome):
            first = recv_type(ws, "cloud")
            assert first["seq"] == welcome["seq"] + 1  # right after welcome
            assert first["state"] == "fallback" and first["reason"] == "network"


def test_no_cloud_message_before_any_state(hub_env):
    with page(hub_env.client, "lens") as (ws, _):
        hub_env.bus.publish(C.CAPTION, caption_event("marker"))
        seen: list = []
        recv_type(ws, "caption", seen=seen)
        assert not [m for m in seen if isinstance(m, dict) and m.get("type") == "cloud"]


def test_event_log_notes_only_real_changes(hub_env):
    with page(hub_env.client, "console") as (console, _):
        bus = hub_env.bus
        bus.publish(C.CLOUD_STATE, cloud_state(enabled=False, state="off", latency_ms=None))
        bus.publish(C.CLOUD_STATE, cloud_state(state="connecting", latency_ms=None))
        bus.publish(C.CLOUD_STATE, cloud_state(state="on", latency_ms=400))
        bus.publish(C.CLOUD_STATE, cloud_state(state="on", latency_ms=430))  # latency only
        bus.publish(C.CLOUD_STATE, cloud_state(state="fallback", reason="network"))
        bus.publish(C.CLOUD_STATE, cloud_state(enabled=False, state="off", latency_ms=None))
        bus.publish(C.CAPTION, caption_event("marker"))
        seen: list = []
        recv_type(console, "caption", seen=seen)
        logs = [m["text"] for m in seen if isinstance(m, dict) and m.get("type") == "event_log"]
        assert logs == [
            "Cloud captions: connecting",
            "Cloud captions on",
            "Cloud captions: fell back to local (network)",
            "Cloud captions off",
        ]
        assert len([m for m in seen if isinstance(m, dict) and m.get("type") == "cloud"]) == 6


def test_cloud_set_is_routed_to_the_bus(hub_env):
    assert "cloud.set" in C.COMMAND_NAMES
    with page(hub_env.client, "phone") as (ws, _):
        ws.send_json({"type": "command", "name": "cloud.set", "args": {"on": True}})
        ws.send_json(
            {"type": "command", "name": "cloud.set", "args": {"on": True, "language": "es-US"}}
        )
        assert wait_for(lambda: len(hub_env.rec[C.COMMAND]) == 2)
    got = [(e["name"], e["args"]) for e in hub_env.rec[C.COMMAND]]
    assert got == [
        ("cloud.set", {"on": True}),
        ("cloud.set", {"on": True, "language": "es-US"}),
    ]
    assert hub_env.router.ignored == 0


def test_engine_starts_cloud_captions_right_after_audio():
    config = {"engine": {"data_dir": "data"}, "pages": {}}
    engine = M.Engine(M.Options(no_browser=True), config)
    names = [name for name, _ in engine._steps()]
    assert names[names.index("audio") + 1] == "cloud"
    assert names.count("cloud") == 1
