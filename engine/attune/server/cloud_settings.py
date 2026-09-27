"""Laptop-only Google key for cloud captions (P-48); the key stays in .env, never on the bus.

Works exactly like the ElevenLabs settings (speech_settings.py, P-41): loopback and
same-origin only, an atomic .env write that keeps every other entry, a blank key keeps the
saved one, and no response or log ever carries the key. The cloud client (A-32) reads
`.env` again when it opens a stream, so a saved key applies without a restart.

    GET  /api/settings/google  {key_configured, key_source, service_account, restart_required}
    POST /api/settings/google  {"api_key": "..."}
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .speech_settings import _guard, _write_env

_KEY = "GOOGLE_SPEECH_API_KEY"
_ACCOUNT = "GOOGLE_APPLICATION_CREDENTIALS"
_WHAT = "cloud caption settings"


def _summary(path: Path) -> dict:
    values = dotenv_values(path) if path.exists() else {}
    env_key = (os.environ.get(_KEY) or "").strip()
    file_key = (values.get(_KEY) or "").strip()
    account = (os.environ.get(_ACCOUNT) or "").strip() or (values.get(_ACCOUNT) or "").strip()
    has_account = False
    if account:
        file = Path(account)
        if not file.is_absolute():
            file = path.parent / file
        has_account = file.is_file()  # only whether it exists; the path is never returned
    return {
        "key_configured": bool(env_key or file_key),
        "key_source": "environment" if env_key else "file" if file_key else "none",
        "service_account": has_account,
        "restart_required": False,
    }


def _save(path: Path, changes: dict[str, str]) -> dict:
    if changes:
        _write_env(path, changes)
    return _summary(path)


def create_router(root: Path) -> APIRouter:
    """Create the Google key routes using the engine working directory's .env."""
    router = APIRouter(prefix="/api/settings/google")
    path = root / ".env"

    @router.get("", include_in_schema=False)
    async def read(request: Request) -> JSONResponse:
        _guard(request, _WHAT)
        try:
            result = await run_in_threadpool(_summary, path)
        except (OSError, UnicodeError):
            raise HTTPException(500, "Could not read local cloud caption settings.") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("", include_in_schema=False)
    async def save(request: Request) -> JSONResponse:
        _guard(request, _WHAT)
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
        if not isinstance(body, dict) or set(body) - {"api_key"}:
            raise HTTPException(400, "Invalid settings fields.")
        changes = {}
        if "api_key" in body:
            value = body["api_key"]
            if not isinstance(value, str) or len(value) > 4096:
                raise HTTPException(400, "Invalid settings value.")
            value = value.strip()
            if value and (not value.isascii() or not all(c.isalnum() or c in "_-" for c in value)):
                raise HTTPException(400, "Use only letters, numbers, underscores and hyphens.")
            if value:  # an empty password field keeps the saved key
                changes[_KEY] = value
        try:
            result = await run_in_threadpool(_save, path, changes)
        except (OSError, UnicodeError):
            raise HTTPException(500, "Could not save local cloud caption settings.") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    return router
