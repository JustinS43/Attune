"""P-35: the phone's station screens in a real browser (390x844, Microsoft Edge, headless).

Starts tests/pages_engine/station_e2e/fake_engine.py (the real hub, save flow and station with
fake devices and models) and runs station_e2e/phone_station.mjs against it. Skipped when
node, playwright-core or Edge isn't there. playwright-core: set PLAYWRIGHT_CORE to its folder
(default: the attune-video render tools next to the main checkout, if present).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent / "station_e2e"
ROOT = HERE.parents[2]
EDGE = [
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Microsoft/Edge/Application/msedge.exe",
]


def playwright_core() -> Path | None:
    env = os.environ.get("PLAYWRIGHT_CORE")
    candidates = [Path(env)] if env else []
    for base in (ROOT, *ROOT.parents[:2]):
        candidates.append(base / "attune-video/render/node_modules/playwright-core")
    return next((p for p in candidates if (p / "package.json").is_file()), None)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


PW = playwright_core()
pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and PW and any(p.is_file() for p in EDGE)),
    reason="node, playwright-core or Microsoft Edge not available",
)


def test_phone_station_screens(tmp_path):
    port = int(os.environ.get("ATTUNE_E2E_PORT", "0")) or free_port()
    data = tmp_path / "data"
    engine = subprocess.Popen(
        [sys.executable, str(HERE / "fake_engine.py"), "--port", str(port), "--data", str(data)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        ready = False
        while time.monotonic() < deadline and not ready:
            line = engine.stdout.readline()
            if not line and engine.poll() is not None:
                break
            ready = line.startswith("READY")
        assert ready, "the fake engine did not start"
        env = {**os.environ, "PLAYWRIGHT_CORE": str(PW)}
        shots = os.environ.get("ATTUNE_E2E_SHOTS", "")  # a folder to keep screenshots in
        run = subprocess.run(
            ["node", str(HERE / "phone_station.mjs"), f"http://127.0.0.1:{port}", shots],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        print(run.stdout)
        assert run.returncode == 0, run.stdout + run.stderr
        assert "PASS phone station screens" in run.stdout
    finally:
        engine.terminate()
        try:
            engine.wait(timeout=5)
        except subprocess.TimeoutExpired:
            engine.kill()
        shutil.rmtree(data, ignore_errors=True)
