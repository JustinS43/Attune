"""Laptop-only ElevenLabs settings; secrets stay in .env, never on the bus."""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
import threading
from pathlib import Path

from dotenv import dotenv_values, set_key
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

_LOCK = threading.Lock()
_KEY = "ELEVENLABS_API_KEY"
_VOICE = "ELEVENLABS_VOICE_ID"


def _local(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _guard(request: Request, what: str = "ElevenLabs settings") -> None:
    """Require laptop access and an exact same-origin browser request."""
    if not request.client or not _local(request.client.host) or not _local(request.url.hostname):
        raise HTTPException(403, f"Manage {what} on the Attune laptop.")
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origin and origin != expected:
        raise HTTPException(403, "Open Settings from this Attune engine.")
    if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise HTTPException(403, "Open Settings from this Attune engine.")
    if request.method != "GET" and origin != expected:
        raise HTTPException(403, "Open Settings from this Attune engine.")


def _summary(path: Path) -> dict:
    values = dotenv_values(path) if path.exists() else {}
    env_key = (os.environ.get(_KEY) or "").strip()
    file_key = (values.get(_KEY) or "").strip()
    env_voice = (os.environ.get(_VOICE) or "").strip()
    return {
        "key_configured": bool(env_key or file_key),
        "key_source": "environment" if env_key else "file" if file_key else "none",
        "voice_id": env_voice or (values.get(_VOICE) or "").strip(),
        "voice_from_environment": bool(env_voice),
    }


def _write_env(path: Path, changes: dict[str, str]) -> None:
    """Set `changes` in the .env at `path` atomically (also used by cloud_settings, P-47)."""
    with _LOCK:
        # Build the replacement privately and swap once, preserving unrelated .env entries.
        fd, name = tempfile.mkstemp(prefix=".env.settings-", dir=path.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, "wb") as output:
                if path.exists():
                    output.write(path.read_bytes())
            for key, value in changes.items():
                set_key(temp, key, value, quote_mode="always")
            if path.exists():
                os.chmod(temp, path.stat().st_mode)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def _save(path: Path, changes: dict[str, str]) -> dict:
    _write_env(path, changes)
    return {**_summary(path), "restart_required": False}


def create_router(root: Path) -> APIRouter:
    """Create settings routes using the engine working directory's .env."""
    router = APIRouter(prefix="/api/settings/elevenlabs")
    path = root / ".env"

    @router.get("", include_in_schema=False)
    async def read(request: Request) -> JSONResponse:
        _guard(request)
        try:
            result = await run_in_threadpool(_summary, path)
        except (OSError, UnicodeError):
            raise HTTPException(500, "Could not read local speech settings.") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("", include_in_schema=False)
    async def save(request: Request) -> JSONResponse:
        _guard(request)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "Send settings as JSON.")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise HTTPException(413, "Settings are too long.")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid settings.") from None
        if not isinstance(body, dict) or set(body) - {"api_key", "voice_id"}:
            raise HTTPException(400, "Invalid settings fields.")
        changes = {}
        for field, variable, limit in (("api_key", _KEY, 4096), ("voice_id", _VOICE, 256)):
            if field not in body:
                continue
            value = body[field]
            if not isinstance(value, str) or len(value) > limit:
                raise HTTPException(400, "Invalid settings value.")
            value = value.strip()
            if value and (not value.isascii() or not all(c.isalnum() or c in "_-" for c in value)):
                raise HTTPException(400, "Use only letters, numbers, underscores and hyphens.")
            # Empty password means keep the existing key; blank voice restores the default.
            if field != "api_key" or value:
                changes[variable] = value
        try:
            result = await run_in_threadpool(_save, path, changes)
        except (OSError, UnicodeError):
            raise HTTPException(500, "Could not save local speech settings.") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return router
