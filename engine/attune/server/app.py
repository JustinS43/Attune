"""The engine's web server: static pages, the WebSocket hub and the history API.

Section 4 - Pages, Engine & Demo. TODO: P-03. Contracts: docs/contracts.md (3, 5, 8).

Routes:
    /                  -> redirect to /lens/
    /ws                WebSocket hub (server/ws.py)
    /lens/ /panels/ /shared/ /phone/   the repo's web/ folders
    /data/reels/film/  demo reels from <cwd>/data/reels/film (only that folder:
                       people, profiles, sessions and history are never served)
    /api/history/...   Section 3's history router, when it exists
    /api/settings/elevenlabs, /api/settings/google   laptop-only keys in .env (P-41, P-48)
    /api/sim/...       simulated-Arduino controls, only with --simulate-hardware
    /favicon.ico       the Attune mark (pages without their own icon)

No API schema is published (/openapi.json, /docs). With `allowed_hosts` (the engine passes
the loopback names when it listens on 127.0.0.1) a request whose Host header names any
other host is refused with 400, so a website can't reach the engine through DNS rebinding
(P-38).

`WebServer` runs uvicorn on its own thread so bus callbacks never wait on it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .cloud_settings import create_router as cloud_settings_router
from .speech_settings import create_router as speech_settings_router
from .ws import Hub, host_of, is_loopback_host

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[3] / "web"
PAGE_FOLDERS = ("lens", "panels", "shared", "phone", "demo")
REELS = Path("data") / "reels" / "film"


SIM_GESTURES = ("tap", "hold", "double", "triple")
LOOPBACK_NAMES = ["localhost", "127.0.0.1", "::1", "*.localhost"]


def local_hosts(bind_host: str, extra: list[str] | tuple[str, ...] = ()) -> list[str] | None:
    """Host names the engine answers to: the loopback names when it listens on loopback,
    else None (any name: the phone reaches a LAN address the laptop can't list reliably)."""
    if not is_loopback_host(str(bind_host).strip().strip("[]").lower()):
        return None
    return [*LOOPBACK_NAMES, *(str(h).lower() for h in extra)]


class HostGuard:
    """ASGI middleware: refuse requests (HTTP and WebSocket) for a host not in `allowed`.

    Like Starlette's TrustedHostMiddleware, but it understands `[::1]:8000` and treats
    `*.localhost` and any 127.x address as loopback."""

    def __init__(self, app: Any, allowed: list[str]) -> None:
        self.app = app
        self.names = {h for h in allowed if not h.startswith("*.")}
        self.suffixes = tuple(h[1:] for h in allowed if h.startswith("*."))
        self._warned: set[str] = set()

    def ok(self, host: str) -> bool:
        if not host:
            return False
        if host in self.names or host.endswith(self.suffixes):
            return True
        return "127.0.0.1" in self.names and is_loopback_host(host)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        raw = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1")
        if self.ok(host_of(raw)):
            await self.app(scope, receive, send)
            return
        if raw not in self._warned and len(self._warned) < 50:
            self._warned.add(raw)
            log.warning(
                "Refused a request for host %r: the engine only answers to %s",
                raw,
                ", ".join(sorted(self.names | {"*" + s for s in self.suffixes})),
            )
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await Response("Invalid host header", 400, media_type="text/plain")(scope, receive, send)


def mount_sim_routes(app: FastAPI, hub: Hub) -> None:
    """Simulated-Arduino controls, only with --simulate-hardware (tests and demos).

    POST /api/sim/touch   {"gesture": "tap" | "hold" | "double" | "triple"}
    POST /api/sim/control {"heartbeat": false}, {"sound": {"left": 300, "right": 0}},
                          {"reboot": true} (a brown-out reset)
    GET  /api/sim/state   the simulator's snapshot from the hardware status (lines it got,
                          icon, pattern, ACKs)

    POSTs must be JSON, so another website can't trigger them from a form (no CORS here).
    """

    async def body(request: Request) -> dict[str, Any]:
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(415, "send JSON")
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, "bad JSON") from None
        if not isinstance(data, dict):
            raise HTTPException(400, "send a JSON object")
        return data

    @app.post("/api/sim/touch", include_in_schema=False)
    async def sim_touch(request: Request) -> dict[str, Any]:
        gesture = str((await body(request)).get("gesture", "")).lower()
        if gesture not in SIM_GESTURES:
            raise HTTPException(400, f"gesture must be one of {', '.join(SIM_GESTURES)}")
        hub.bus.publish("hw.sim_touch", {"gesture": gesture})
        return {"ok": True, "gesture": gesture}

    @app.post("/api/sim/control", include_in_schema=False)
    async def sim_control(request: Request) -> dict[str, Any]:
        data = await body(request)
        event: dict[str, Any] = {}
        if isinstance(data.get("heartbeat"), bool):
            event["heartbeat"] = data["heartbeat"]
        if isinstance(data.get("sound"), dict):
            event["sound"] = data["sound"]
        if data.get("reboot") is True:
            event["reboot"] = True
        if not event:
            raise HTTPException(
                400, "send heartbeat (bool), sound {left, right} and/or reboot (true)"
            )
        hub.bus.publish("hw.sim_control", event)
        return {"ok": True, **event}

    @app.get("/api/sim/state", include_in_schema=False)
    async def sim_state() -> JSONResponse:
        part = ((hub.latest_status or {}).get("parts") or {}).get("hardware") or {}
        sim = (part.get("metrics") or {}).get("sim")
        if sim is None:
            return JSONResponse({"ok": False, "detail": "no simulator status yet"}, 503)
        return JSONResponse(
            {
                "ok": True,
                "part_ok": part.get("ok"),
                "detail": part.get("detail"),
                "metrics": {k: v for k, v in part["metrics"].items() if k != "sim"},
                "sim": sim,
            }
        )

    log.info("Simulated-Arduino controls mounted at /api/sim")


class _RevalidatedStatic(StaticFiles):
    """Page files the browser must check (ETag) before reusing, so an updated lens or phone
    script always loads on a normal reload, including inside the demo page's frames. Without
    this, browsers guess a freshness time and keep running old modules."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    hub: Hub,
    web_root: str | Path | None = None,
    data_root: str | Path | None = None,
    history_router: Any = None,
    sim_routes: bool = False,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    """Build the FastAPI app around `hub`. `data_root` defaults to the working directory.

    `sim_routes` adds the simulated-Arduino controls (see `mount_sim_routes`).
    `allowed_hosts` (see `local_hosts`) turns on the Host-header guard; None leaves it off."""
    web = Path(web_root) if web_root else WEB_ROOT
    reels = (Path(data_root) if data_root else Path.cwd()) / REELS

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.attach(asyncio.get_running_loop())
        try:
            yield
        finally:
            hub.detach()

    app = FastAPI(
        title="Attune engine", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    if allowed_hosts is not None:
        app.add_middleware(HostGuard, allowed=list(allowed_hosts))

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/lens/")

    icon = web / "phone" / "favicon.svg"

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        if icon.is_file():
            return FileResponse(icon, media_type="image/svg+xml")
        return Response(status_code=204)

    app.add_api_websocket_route("/ws", hub.endpoint)
    settings_root = Path(data_root) if data_root else Path.cwd()
    app.include_router(speech_settings_router(settings_root))
    app.include_router(cloud_settings_router(settings_root))  # the Google key (P-48)

    if history_router is not None:
        app.include_router(history_router, prefix="/api/history")
        log.info("History API mounted at /api/history")
    if sim_routes:
        mount_sim_routes(app, hub)

    for name in PAGE_FOLDERS:
        folder = web / name
        if folder.is_dir():
            app.mount(f"/{name}", _RevalidatedStatic(directory=folder, html=True), name=name)
        else:
            log.warning("Page folder missing: %s", folder)
    if reels.is_dir():
        app.mount("/data/reels/film", StaticFiles(directory=reels), name="reels")
    return app


class WebServer:
    """uvicorn on a daemon thread; `start()` returns once it is listening."""

    def __init__(self, app: FastAPI, host: str = "127.0.0.1", port: int = 8000) -> None:
        import uvicorn

        self.host, self.port = host, port
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="warning",
                lifespan="on",
                timeout_graceful_shutdown=1,
                ws_ping_interval=20,
            )
        )
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout_s: float = 10.0) -> None:
        self._thread = threading.Thread(target=self._serve, name="web-server", daemon=True)
        self._thread.start()
        end = time.monotonic() + timeout_s
        while not self.server.started:
            if not self._thread.is_alive():
                raise RuntimeError(f"Web server could not start on {self.url} (port in use?)")
            if time.monotonic() > end:
                raise RuntimeError(f"Web server did not start on {self.url} in {timeout_s} s")
            time.sleep(0.05)
        log.info("Web server listening on %s", self.url)

    def _serve(self) -> None:
        try:
            self.server.run()
        except SystemExit:  # uvicorn exits this way when it can't bind
            pass
        except Exception:
            log.exception("Web server stopped with an error")

    def stop(self) -> None:
        self.server.should_exit = True
        if self._thread:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                self.server.force_exit = True
                self._thread.join(timeout=0.5)
