"""Starts and stops every service: `python -m attune`.

Section 4 - Pages, Engine & Demo. TODO: P-02. Contracts: docs/contracts.md (1).

Loads the config, creates the bus and clock, then each section's service in the
contract order: hardware, audio, vision, fusion, alerts, llm, (calibration),
speech_out, history, server. A service that is missing or fails to start is
logged, reported as a `status.part` with ok=false, and skipped; the rest keep
running. Ctrl+C stops everything within about 3 seconds.

    python -m attune                                  # webcam by name, mic, browser
    python -m attune --source data/reels/film/cafe_friends.mp4 --audio-file talk.wav
    python -m attune --source 1 --no-mic --no-browser --port 8001
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import logging
import os
import signal
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_config, section
from .core import clock
from .core.bus import Bus
from .core.contracts import STATUS_PART
from .core.session_log import SessionLog, new_session_id
from .core.status import StatusAggregator
from .server.commands import CommandRouter
from .server.ws import Hub

log = logging.getLogger("attune")

STOP_BUDGET_S = 3.0


@dataclass
class Options:
    config: str | None = None
    source: str | int | None = None
    audio_file: str | None = None
    repeat_audio: float = 0.0
    audio_delay: float = 3.0
    no_mic: bool = False
    host: str | None = None
    port: int | None = None
    no_browser: bool = False
    simulate_hardware: bool = False
    seconds: float | None = None


def parse_args(argv: list[str] | None = None) -> Options:
    ap = argparse.ArgumentParser(
        prog="python -m attune",
        description="Attune engine: captions, faces, alerts and the web pages.",
    )
    ap.add_argument("--config", help="config file (default: config/attune.toml)")
    ap.add_argument("--source", help="video file or camera index instead of the named webcam")
    ap.add_argument(
        "--audio-file", help="feed this 16-bit WAV as the microphone (implies --no-mic)"
    )
    ap.add_argument(
        "--repeat-audio",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="play --audio-file again this many seconds after it ends (0 = once)",
    )
    ap.add_argument(
        "--audio-delay",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="wait this long after start-up before playing --audio-file",
    )
    ap.add_argument("--no-mic", action="store_true", help="don't open the microphone")
    ap.add_argument("--host", help="web server address (default: [engine] host, 127.0.0.1)")
    ap.add_argument("--port", type=int, help="web server port (default: [engine] port, 8000)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the lens page")
    ap.add_argument(
        "--simulate-hardware",
        action="store_true",
        help="run Section 3's simulated Arduino instead of the USB one",
    )
    ap.add_argument("--seconds", type=float, help=argparse.SUPPRESS)  # quit by itself (tests)
    a = ap.parse_args(argv)
    source: str | int | None = a.source
    if isinstance(source, str) and source.isdigit():
        source = int(source)
    return Options(
        config=a.config,
        source=source,
        audio_file=a.audio_file,
        repeat_audio=a.repeat_audio,
        audio_delay=a.audio_delay,
        no_mic=a.no_mic or bool(a.audio_file),
        host=a.host,
        port=a.port,
        no_browser=a.no_browser,
        simulate_hardware=a.simulate_hardware,
        seconds=a.seconds,
    )


def preload_torch() -> None:
    """Import torch before onnxruntime so both share torch's cuDNN (see vision/runtime.py)."""
    if importlib.util.find_spec("torch") is None:
        return
    try:
        import torch  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - vision and the CPU paths work without it
        log.warning("Could not load torch before onnxruntime: %s", exc)


def _load_class(module: str, name: str) -> type | None:
    """A service class, or None when its section hasn't built it yet."""
    try:
        mod = importlib.import_module(module)
    except ImportError as exc:
        log.warning("%s unavailable: %s", module, exc)
        return None
    cls = getattr(mod, name, None)
    if cls is None:
        log.info("%s.%s is not built yet; skipping it", module, name)
    return cls


class Engine:
    """Owns the bus, the services and the web server for one run."""

    def __init__(self, opts: Options, config: dict[str, Any] | None = None) -> None:
        self.opts = opts
        self.config = config if config is not None else load_config(opts.config)
        if opts.simulate_hardware:
            self.config["hardware"] = {**section(self.config, "hardware"), "simulate": True}
        engine_cfg = section(self.config, "engine")
        self.host = opts.host or engine_cfg.get("host", "127.0.0.1")
        self.port = int(opts.port or engine_cfg.get("port", 8000))
        self.data_dir = Path(engine_cfg.get("data_dir", "data"))
        self.bus = Bus()
        self.session_id = new_session_id()
        self.config["session_id"] = self.session_id
        self.session_log = SessionLog(
            self.bus, self.data_dir / "sessions", self.session_id, clock.now
        )
        self.router = CommandRouter(self.bus, self.session_log)
        self.hub = Hub(self.bus, self.config, self.session_id, self.router, clock.now)
        self.status = StatusAggregator(self.bus, self.config, clock.now)
        self.running: list[tuple[str, Any]] = []
        self.failed: dict[str, str] = {}
        self.web = None
        self.stop_event = threading.Event()

    # ---- service table (contract order) ----
    def _steps(self) -> list[tuple[str, Callable[[], Any | None]]]:
        bus, config, opts = self.bus, self.config, self.opts

        def make(module: str, name: str, **kwargs: Any) -> Callable[[], Any | None]:
            def build() -> Any | None:
                cls = _load_class(module, name)
                return None if cls is None else cls(bus, config, **kwargs)

            return build

        return [
            ("hardware", make("attune.hardware.service", "HardwareService")),
            (
                "audio",
                make("attune.audio.service", "AudioService", mic=False if opts.no_mic else None),
            ),
            (
                "vision",
                make("attune.vision.service", "VisionService", clock=clock.now, source=opts.source),
            ),
            ("fusion", make("attune.fusion.service", "FusionService", clock=clock.now)),
            ("alerts", make("attune.alerts.service", "AlertService")),
            ("llm", make("attune.llm.service", "LLMService")),
            ("calibration", make("attune.calibration.service", "CalibrationService")),
            ("speech_out", make("attune.speech_out.service", "SpeechOutService")),
            ("history", make("attune.history.service", "HistoryService")),
        ]

    def _fail(self, name: str, detail: str) -> None:
        self.failed[name] = detail
        self.bus.publish(STATUS_PART, {"part": name, "ok": False, "detail": detail, "metrics": {}})

    def start(self) -> None:
        # Engine-side listeners subscribe first so they see every service's first events.
        self.router.connect()
        self.hub.connect()
        self.status.start()
        self.session_log.start()
        for name, build in self._steps():
            t0 = time.perf_counter()
            try:
                svc = build()
            except Exception as exc:
                log.exception("Could not create %s", name)
                self._fail(name, f"could not create: {exc}")
                continue
            if svc is None:
                self._fail(name, "not built yet")
                continue
            try:
                svc.start()
            except Exception as exc:
                log.exception("Could not start %s; continuing without it", name)
                self._fail(name, f"failed to start: {exc}")
                try:
                    svc.stop()
                except Exception as stop_exc:  # noqa: BLE001
                    log.debug("Stopping %s after a failed start: %s", name, stop_exc)
                continue
            self.running.append((name, svc))
            log.info("Started %s (%.1f s)", name, time.perf_counter() - t0)
        if self.opts.audio_file:
            self._start_player()
        self._start_server()

    def _start_player(self) -> None:
        from .replay.player import FilePlayer

        try:
            player = FilePlayer(
                self.bus,
                self.opts.audio_file,
                clock.now,
                delay_s=self.opts.audio_delay,
                repeat_s=self.opts.repeat_audio,
            )
            player.start()
            self.running.append(("audio_file", player))
        except Exception as exc:
            log.exception("Could not play %s", self.opts.audio_file)
            self._fail("audio_file", f"could not play: {exc}")

    def _start_server(self) -> None:
        from .server.app import WebServer, create_app

        history_router = None
        api = _load_module("attune.history.api")
        if api is not None:
            history_router = getattr(api, "router", None)
            if history_router is None:
                log.info("attune.history.api.router is not built yet; no /api/history")
        try:
            app = create_app(self.hub, history_router=history_router)
            self.hub.start()
            self.web = WebServer(app, self.host, self.port)
            self.web.start()
            self.running.append(("server", self.web))
        except Exception as exc:
            log.exception("Web server failed")
            self._fail("server", str(exc))

    def stop(self) -> None:
        """Stop everything in parallel (reverse order of starting), within ~3 s."""
        log.info("Stopping...")
        deadline = time.monotonic() + STOP_BUDGET_S
        items = list(reversed(self.running)) + [("hub", self.hub), ("status", self.status)]
        threads = []
        for name, svc in items:
            t = threading.Thread(
                target=_safe_stop, args=(name, svc), name=f"stop-{name}", daemon=True
            )
            t.start()
            threads.append((name, t))
        for name, t in threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
            if t.is_alive():
                log.warning("%s did not stop in time", name)
        self.router.close()
        self.session_log.stop()
        self.running = []

    def wait(self) -> None:
        end = time.monotonic() + self.opts.seconds if self.opts.seconds else None
        while not self.stop_event.wait(0.25):
            if end is not None and time.monotonic() >= end:
                break

    @property
    def lens_url(self) -> str:
        return f"{self.base_url}/lens/"

    @property
    def demo_url(self) -> str:
        """The demo page: glasses view and phone app side by side."""
        return f"{self.base_url}/demo/"

    @property
    def base_url(self) -> str:
        host = "localhost" if self.host in ("127.0.0.1", "0.0.0.0") else self.host
        return f"http://{host}:{self.port}"


def _load_module(module: str):
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        log.warning("%s unavailable: %s", module, exc)
        return None


def _safe_stop(name: str, svc: Any) -> None:
    try:
        svc.stop()
    except Exception:
        log.exception("Stopping %s failed", name)


def run(opts: Options) -> int:
    preload_torch()
    engine = Engine(opts)

    def on_signal(signum, frame):
        engine.stop_event.set()

    for sig in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), on_signal)
    try:
        engine.start()
        if engine.web is not None:
            log.info(
                "Attune is running: %s (glasses + phone), %s (glasses only). Ctrl+C to stop",
                engine.demo_url,
                engine.lens_url,
            )
            if not opts.no_browser:
                webbrowser.open(engine.demo_url)
        if engine.failed:
            log.warning("Running without: %s", ", ".join(sorted(engine.failed)))
        engine.wait()
    except KeyboardInterrupt:
        pass
    finally:
        t0 = time.monotonic()
        try:
            engine.stop()
        except KeyboardInterrupt:
            pass
        log.info("Stopped in %.1f s", time.monotonic() - t0)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("ATTUNE_LOG", "INFO").upper(),
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    code = run(parse_args(argv))
    logging.shutdown()
    # Model runtimes (onnxruntime, torch, PortAudio) can leave non-daemon threads behind;
    # don't let them hold the terminal after a clean stop.
    stragglers = [
        t for t in threading.enumerate() if t is not threading.main_thread() and not t.daemon
    ]
    if stragglers:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    return code
