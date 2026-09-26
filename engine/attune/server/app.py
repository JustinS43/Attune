"""The engine's web server: static pages, the WebSocket hub and the history API.

Section 4 - Pages, Engine & Demo. TODO: P-03. Contracts: docs/contracts.md (3, 5, 8).

Routes:
    /                  -> redirect to /lens/
    /ws                WebSocket hub (server/ws.py)
    /lens/ /panels/ /shared/ /phone/   the repo's web/ folders
    /data/reels/film/  demo reels from <cwd>/data/reels/film (only that folder:
                       people, profiles, sessions and history are never served)
    /api/history/...   Section 3's history router, when it exists

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

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .speech_settings import create_router as speech_settings_router
from .ws import Hub

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[3] / "web"
PAGE_FOLDERS = ("lens", "panels", "shared", "phone", "demo")
REELS = Path("data") / "reels" / "film"


def create_app(
    hub: Hub,
    web_root: str | Path | None = None,
    data_root: str | Path | None = None,
    history_router: Any = None,
) -> FastAPI:
    """Build the FastAPI app around `hub`. `data_root` defaults to the working directory."""
    web = Path(web_root) if web_root else WEB_ROOT
    reels = (Path(data_root) if data_root else Path.cwd()) / REELS

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.attach(asyncio.get_running_loop())
        try:
            yield
        finally:
            hub.detach()

    app = FastAPI(title="Attune engine", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/lens/")

    app.add_api_websocket_route("/ws", hub.endpoint)
    app.include_router(speech_settings_router(Path(data_root) if data_root else Path.cwd()))

    if history_router is not None:
        app.include_router(history_router, prefix="/api/history")
        log.info("History API mounted at /api/history")

    for name in PAGE_FOLDERS:
        folder = web / name
        if folder.is_dir():
            app.mount(f"/{name}", StaticFiles(directory=folder, html=True), name=name)
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
