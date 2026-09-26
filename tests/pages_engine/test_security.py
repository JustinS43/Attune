"""Who may talk to the engine: WebSocket origins, host names, the API schema, sim routes.

Section 4 - Pages, Engine & Demo. TODO: P-38.
"""

from __future__ import annotations

import pytest
from attune.core import contracts as C
from attune.server.app import create_app, local_hosts
from attune.server.ws import Hub, is_loopback_host, origin_allowed

from .conftest import CONFIG, Recorder, recv_type, wait_for

# ---------------------------------------------------------------- pure checks


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "127.8.9.10", "::1", "attune.localhost", "LOCALHOST"],
)
def test_loopback_hosts(host):
    assert is_loopback_host(host.lower())


@pytest.mark.parametrize(
    "host", ["evil.example", "192.168.1.20", "localhost.evil.example", ""]
)
def test_not_loopback(host):
    assert not is_loopback_host(host)


@pytest.mark.parametrize(
    ("origin", "host", "ok"),
    [
        (None, "localhost:8000", True),  # not a browser: scripts, tests
        ("http://localhost:8000", "localhost:8000", True),
        ("http://127.0.0.1:8013", "127.0.0.1:8013", True),
        (
            "http://localhost:5173",
            "127.0.0.1:8000",
            True,
        ),  # a page served by a dev server
        ("http://[::1]:8000", "[::1]:8000", True),
        ("http://192.168.1.20:8000", "192.168.1.20:8000", True),  # phone on the LAN
        ("http://evil.example", "localhost:8000", False),
        ("https://evil.example:8000", "127.0.0.1:8000", False),
        ("null", "localhost:8000", False),  # sandboxed iframe or file:// page
        ("http://localhost.evil.example", "localhost:8000", False),
        ("not a url", "localhost:8000", False),
    ],
)
def test_origin_allowed(origin, host, ok):
    assert origin_allowed(origin, host) is ok


def test_origin_allowed_by_config():
    assert origin_allowed(
        "https://demo.example", "localhost:8000", ["https://demo.example"]
    )
    assert not origin_allowed(
        "https://demo.example:444", "localhost:8000", ["https://demo.example"]
    )


def test_local_hosts_for_loopback_and_lan():
    assert local_hosts("127.0.0.1") == ["localhost", "127.0.0.1", "::1", "*.localhost"]
    assert local_hosts("127.0.0.1", ["attune.lan"])[-1] == "attune.lan"
    assert local_hosts("0.0.0.0") is None  # LAN mode: the phone may use any address
    assert local_hosts("192.168.1.20") is None


# ---------------------------------------------------------------- through the app


@pytest.fixture
def guarded(tmp_path):
    """An app with the host guard on, as `python -m attune` builds it on 127.0.0.1."""
    from attune.core.bus import Bus
    from fastapi.testclient import TestClient

    bus = Bus()
    rec = Recorder(bus, C.COMMAND)
    hub = Hub(bus, CONFIG, "test-session", data_dir=tmp_path / "data")
    hub.connect()
    hub.start()
    app = create_app(
        hub, data_root=tmp_path, allowed_hosts=local_hosts("127.0.0.1"), sim_routes=True
    )
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client, bus, rec
    hub.stop()


def test_no_api_schema(hub_env):
    assert hub_env.client.get("/openapi.json").status_code == 404
    assert hub_env.client.get("/docs").status_code == 404


def test_other_host_names_are_refused(guarded):
    client, _, _ = guarded
    assert client.get("/lens/").status_code == 200
    assert client.get("/lens/", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/lens/", headers={"host": "attune.localhost"}).status_code == 200
    r = client.get("/lens/", headers={"host": "evil.example:8000"})
    assert r.status_code == 400
    assert client.get("/lens/", headers={"host": ""}).status_code == 400


def refused(client, **headers) -> bool:
    """Does the hub refuse a page that connects with these headers (and say hello)?"""
    try:
        with client.websocket_connect("/ws", headers=headers) as ws:
            ws.send_json({"type": "hello", "role": "console"})
            ws.send_json({"type": "command", "name": "session.forget", "args": {}})
            recv_type(ws, "welcome", 1.0)
    except Exception:  # noqa: BLE001 - closed before accept (TestClient raises its own types)
        return True
    return False


def test_other_host_names_cant_open_the_websocket(guarded):
    client, _, _ = guarded
    assert refused(client, host="evil.example:8000")
    assert not refused(client, host="localhost:8000")


def test_websocket_refuses_other_websites(hub_env):
    client = hub_env.client
    assert refused(client, origin="http://evil.example")
    assert refused(client, origin="null")
    assert not hub_env.hub.clients
    # the refused pages' commands never reached the bus
    assert not wait_for(lambda: hub_env.rec[C.SESSION_FORGET], 0.3)


def test_websocket_accepts_the_laptops_own_pages(hub_env):
    client = hub_env.client
    for origin in (
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://testserver",
    ):
        with client.websocket_connect("/ws", headers={"origin": origin}) as ws:
            ws.send_json({"type": "hello", "role": "console"})
            assert recv_type(ws, "welcome")["session_id"] == "test-session"


# ---------------------------------------------------------------- simulator routes


def test_sim_routes_only_when_asked(hub_env):
    assert hub_env.client.post(
        "/api/sim/touch", json={"gesture": "tap"}
    ).status_code in (404, 405)


def test_sim_touch_publishes(guarded):
    client, bus, _ = guarded
    got = Recorder(bus, "hw.sim_touch")
    assert client.post("/api/sim/touch", json={"gesture": "double"}).status_code == 200
    assert wait_for(lambda: got["hw.sim_touch"])
    assert got["hw.sim_touch"][0]["gesture"] == "double"


def test_sim_touch_refuses_forms_and_bad_gestures(guarded):
    client, _, _ = guarded
    r = client.post("/api/sim/touch", data={"gesture": "tap"})
    assert r.status_code == 415
    assert client.post("/api/sim/touch", json={"gesture": "explode"}).status_code == 400


def test_sim_control_and_state(guarded):
    client, bus, _ = guarded
    got = Recorder(bus, "hw.sim_control")
    body = {"heartbeat": False, "sound": {"left": 300, "right": 0}}
    assert client.post("/api/sim/control", json=body).status_code == 200
    assert wait_for(lambda: got["hw.sim_control"])
    assert got["hw.sim_control"][0]["heartbeat"] is False
    # no hardware status yet: the state route says so instead of guessing
    assert client.get("/api/sim/state").status_code == 503
