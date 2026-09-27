"""The Google key for cloud captions (P-48): saved to .env like P-41, never echoed.

Only obviously fake values are used here."""

import json

import pytest
from attune.server.cloud_settings import create_router
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient

FAKE = "fake-google-key-for-tests"
OLD = "old-fake-google-key"
URL = "/api/settings/google"
HEADERS = {"Origin": "http://localhost:8000"}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_SPEECH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 1234)) as client:
        yield client, tmp_path / ".env"


def test_save_preserves_other_env_entries_and_never_returns_key(settings):
    client, path = settings
    path.write_text(
        "# keep this comment\nELEVENLABS_API_KEY='fake-eleven'\n"
        f"OTHER_VALUE='untouched'\nGOOGLE_SPEECH_API_KEY='{OLD}'\n"
    )
    response = client.post(URL, headers=HEADERS, json={"api_key": FAKE})
    assert response.status_code == 200
    assert response.json() == {
        "key_configured": True,
        "key_source": "file",
        "service_account": False,
        "restart_required": False,
    }
    values = dotenv_values(path)
    assert values["GOOGLE_SPEECH_API_KEY"] == FAKE
    assert values["OTHER_VALUE"] == "untouched"
    assert values["ELEVENLABS_API_KEY"] == "fake-eleven"
    assert "# keep this comment" in path.read_text()
    read = client.get(URL)
    assert read.json()["key_configured"] is True
    for result in (read, response):
        assert FAKE not in result.text and OLD not in result.text
        assert "fake-eleven" not in result.text
        assert result.headers["cache-control"] == "no-store"


def test_blank_key_keeps_the_existing_key(settings):
    client, path = settings
    path.write_text(f"GOOGLE_SPEECH_API_KEY='{OLD}'\n")
    for body in ({"api_key": " "}, {"api_key": ""}, {}):
        response = client.post(URL, headers=HEADERS, json=body)
        assert response.status_code == 200
        assert response.json()["key_configured"] is True
        assert OLD not in response.text
    assert dotenv_values(path)["GOOGLE_SPEECH_API_KEY"] == OLD


def test_new_file_and_environment_key_wins(settings, monkeypatch):
    client, path = settings
    data = client.get(URL).json()
    assert data == {
        "key_configured": False,
        "key_source": "none",
        "service_account": False,
        "restart_required": False,
    }
    assert client.post(URL, headers=HEADERS, json={"api_key": FAKE}).status_code == 200
    monkeypatch.setenv("GOOGLE_SPEECH_API_KEY", "fake-environment-key")
    data = client.get(URL).json()
    assert data["key_source"] == "environment" and data["key_configured"] is True
    assert "fake-environment-key" not in json.dumps(data)
    assert dotenv_values(path)["GOOGLE_SPEECH_API_KEY"] == FAKE


def test_service_account_only_when_the_file_exists_and_path_never_returned(
    settings, tmp_path, monkeypatch
):
    client, path = settings
    account = tmp_path / "outside" / "fake-service-account.json"
    path.write_text(f"GOOGLE_APPLICATION_CREDENTIALS='{account}'\n")
    assert client.get(URL).json()["service_account"] is False  # named but missing
    account.parent.mkdir()
    account.write_text("{}")
    read = client.get(URL)
    assert read.json()["service_account"] is True
    assert read.json()["key_configured"] is False
    assert "fake-service-account" not in read.text and "outside" not in read.text
    path.write_text("")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(account))
    assert client.get(URL).json()["service_account"] is True


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {**HEADERS, "Sec-Fetch-Site": "cross-site"},
        {**HEADERS, "Host": "evil.example"},
    ],
)
def test_refuses_cross_origin_or_rebound_requests(settings, headers):
    client, path = settings
    response = client.post(URL, headers=headers, json={"api_key": FAKE})
    assert response.status_code == 403
    assert FAKE not in response.text
    assert not path.exists()


def test_remote_phone_cannot_read_or_modify_the_key(tmp_path):
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    with TestClient(app, base_url="http://localhost:8000", client=("192.168.1.25", 80)) as client:
        refused = client.get(URL)
        assert refused.status_code == 403
        assert "on the Attune laptop" in refused.json()["detail"]
        assert client.post(URL, headers=HEADERS, json={"api_key": FAKE}).status_code == 403
    assert not (tmp_path / ".env").exists()


@pytest.mark.parametrize(
    "body",
    [
        {"api_key": 42},
        {"api_key": FAKE + "\nOTHER=bad"},
        {"api_key": FAKE + " x"},
        {"api_key": FAKE + "é"},
        {"api_key": FAKE, "voice_id": "v"},
        {"api_key": "x" * 4097},
        [FAKE],
    ],
)
def test_bad_values_are_not_echoed_or_saved(settings, body):
    client, path = settings
    response = client.post(URL, headers=HEADERS, json=body)
    assert response.status_code == 400
    assert FAKE not in response.text
    assert not path.exists()


def test_limits_body_and_rejects_form_posts(settings):
    client, path = settings
    assert client.post(URL, headers=HEADERS, data={"api_key": FAKE}).status_code == 415
    response = client.post(
        URL, headers={**HEADERS, "Content-Type": "application/json"}, content="x" * 8193
    )
    assert response.status_code == 413
    assert not path.exists()


def test_failed_replace_keeps_previous_file(settings, monkeypatch):
    import attune.server.speech_settings as module

    client, path = settings
    original = f"GOOGLE_SPEECH_API_KEY='{OLD}'\n"
    path.write_text(original)

    def fail(*args):
        raise OSError("internal path details")

    monkeypatch.setattr(module.os, "replace", fail)
    response = client.post(URL, headers=HEADERS, json={"api_key": FAKE})
    assert response.status_code == 500
    assert "internal path" not in response.text and FAKE not in response.text
    assert path.read_text() == original
    assert not list(path.parent.glob(".env.settings-*"))


def test_key_never_logged(settings, caplog):
    client, _ = settings
    with caplog.at_level("DEBUG"):
        client.post(URL, headers=HEADERS, json={"api_key": FAKE})
        client.get(URL)
    assert FAKE not in caplog.text


def test_mounted_in_the_engine_app(hub_env):
    # the test client isn't on loopback, so the mounted route refuses it (not a 404)
    response = hub_env.client.get(URL)
    assert response.status_code == 403
    assert "cloud caption settings" in response.json()["detail"]
