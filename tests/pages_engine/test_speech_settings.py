"""Settings persistence and secret boundaries, using only synthetic credentials."""

import json

import pytest
from attune.server.speech_settings import create_router
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    with TestClient(
        app, base_url="http://localhost:8000", client=("127.0.0.1", 1234)
    ) as client:
        yield client, tmp_path / ".env"


URL = "/api/settings/elevenlabs"
HEADERS = {"Origin": "http://localhost:8000"}


def test_save_preserves_other_env_entries_and_never_returns_key(settings):
    client, path = settings
    path.write_text(
        "# keep this comment\nOTHER_VALUE='untouched'\nELEVENLABS_API_KEY='old-key'\n"
    )
    response = client.post(
        URL, headers=HEADERS, json={"api_key": "test-key", "voice_id": "voice123"}
    )
    assert response.status_code == 200
    assert response.json()["restart_required"] is True
    values = dotenv_values(path)
    assert values["OTHER_VALUE"] == "untouched"
    assert values["ELEVENLABS_API_KEY"] == "test-key"
    assert "# keep this comment" in path.read_text()
    read = client.get(URL)
    assert read.json()["voice_id"] == "voice123"
    assert read.json()["key_configured"] is True
    for result in (read, response):
        assert "test-key" not in result.text
        assert "old-key" not in result.text
        assert result.headers["cache-control"] == "no-store"


def test_blank_key_keeps_existing_and_blank_voice_restores_default(settings):
    client, path = settings
    path.write_text(
        "ELEVENLABS_API_KEY='existing-key'\nELEVENLABS_VOICE_ID='old-voice'\n"
    )
    assert (
        client.post(
            URL, headers=HEADERS, json={"api_key": " ", "voice_id": ""}
        ).status_code
        == 200
    )
    assert dotenv_values(path)["ELEVENLABS_API_KEY"] == "existing-key"
    assert dotenv_values(path)["ELEVENLABS_VOICE_ID"] == ""


def test_new_file_and_environment_precedence(settings, monkeypatch):
    client, path = settings
    assert client.get(URL).json()["key_configured"] is False
    client.post(
        URL, headers=HEADERS, json={"api_key": "file-key", "voice_id": "file-voice"}
    )
    monkeypatch.setenv("ELEVENLABS_API_KEY", "environment-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "environment-voice")
    data = client.get(URL).json()
    assert data["key_source"] == "environment"
    assert data["voice_from_environment"] is True
    assert data["voice_id"] == "environment-voice"
    assert "environment-key" not in json.dumps(data)
    assert dotenv_values(path)["ELEVENLABS_API_KEY"] == "file-key"


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
    response = client.post(URL, headers=headers, json={"api_key": "test-key"})
    assert response.status_code == 403
    assert not path.exists()


def test_remote_phone_cannot_read_or_modify_credentials(tmp_path):
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    with TestClient(
        app, base_url="http://localhost:8000", client=("192.168.1.25", 80)
    ) as client:
        assert client.get(URL).status_code == 403
        assert (
            client.post(URL, headers=HEADERS, json={"api_key": "test-key"}).status_code
            == 403
        )


@pytest.mark.parametrize(
    "body",
    [
        {"api_key": 42},
        {"api_key": "test-key\nOTHER=bad"},
        {"api_key": "test-key", "extra": 1},
        {"api_key": "x" * 4097},
        {"voice_id": ["voice"]},
        ["test-key"],
    ],
)
def test_invalid_values_are_not_echoed_or_saved(settings, body):
    client, path = settings
    response = client.post(URL, headers=HEADERS, json=body)
    assert response.status_code == 400
    assert "test-key" not in response.text
    assert not path.exists()


def test_limits_body_and_rejects_form_posts(settings):
    client, path = settings
    assert (
        client.post(URL, headers=HEADERS, data={"api_key": "test-key"}).status_code
        == 415
    )
    response = client.post(
        URL, headers={**HEADERS, "Content-Type": "application/json"}, content="x" * 8193
    )
    assert response.status_code == 413
    assert not path.exists()


def test_failed_replace_keeps_previous_file(settings, monkeypatch):
    import attune.server.speech_settings as module

    client, path = settings
    original = "ELEVENLABS_API_KEY='old-key'\n"
    path.write_text(original)

    def fail(*args):
        raise OSError("internal path details")

    monkeypatch.setattr(module.os, "replace", fail)
    response = client.post(URL, headers=HEADERS, json={"api_key": "new-key"})
    assert response.status_code == 500
    assert "internal path" not in response.text
    assert path.read_text() == original
    assert not list(path.parent.glob(".env.settings-*"))
