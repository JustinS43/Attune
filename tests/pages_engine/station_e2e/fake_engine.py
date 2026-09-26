"""A fake Attune engine for the phone's station screens (P-35 end-to-end test).

The real hub, command router, save flow and enrollment station, served with the real web/
pages; only the laptop camera, mic, face finder, face printer and voice printer are fakes
(tests/vision/station_fakes.py), paced in real time so the screens can be watched. Nothing
is opened: no camera, no mic, no GPU. Prints go to a temporary folder that is deleted on exit.

    python tests/pages_engine/station_e2e/fake_engine.py --port 8011

The glasses "see" two named people, Sam (track 7) and Ana (track 8). The station's camera
always shows Sam, so a save started from track 8 fails the identity check. A name starting
with "Nocam" makes the laptop camera deliver nothing (the fallback screen).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "engine"), str(ROOT / "tests")]

from attune.core import contracts as C
from attune.core.bus import Bus
from attune.core.save_flow import SaveFlow
from attune.core.session_log import SessionLog
from attune.server.app import WebServer, create_app
from attune.server.commands import CommandRouter
from attune.server.ws import Hub
from attune.station.session import StationEnroller, VisionHooks
from vision.station_fakes import (
    FakeEmbedder,
    energy_vad,
    face_det,
    person,
    speech_like,
)

log = logging.getLogger("fake_engine")
SAM = person(7)
GLASSES = {7: ("Sam", SAM), 8: ("Ana", person(8))}


def plan(i: int):
    """Where the face is on laptop-camera frame i (30 fps): off to one side, far, then good."""
    if i < 36:
        return (0.2, 0.45, 260.0)  # "move to the middle"
    if i < 66:
        return (0.5, 0.45, 70.0)  # "come closer"
    wobble = 0.01 * np.sin(i / 7)
    return (0.5 + wobble, 0.45, 250.0)


_GRAIN = [
    np.random.default_rng(k).integers(-18, 19, (720, 1280, 3)).astype(np.int16) for k in range(4)
]


def draw(i: int) -> np.ndarray:
    """A calm backdrop with a simple face where `plan` says (the preview shows it), with a
    little camera grain so the face crops pass the sharpness check."""
    frame = np.empty((720, 1280, 3), np.uint8)
    frame[:] = (196, 206, 214)
    cv2.rectangle(frame, (0, 520), (1280, 720), (150, 160, 170), -1)
    cx, cy, w = plan(i)
    x, y = int(cx * 1280), int(cy * 720)
    cv2.ellipse(frame, (x, y), (int(w / 2), int(w * 0.62)), 0, 0, 360, (150, 180, 220), -1)
    for dx in (-0.2, 0.2):
        cv2.circle(
            frame, (int(x + dx * w), int(y - 0.12 * w)), max(3, int(w * 0.05)), (50, 40, 40), -1
        )
    cv2.ellipse(
        frame, (x, int(y + 0.25 * w)), (int(w * 0.15), int(w * 0.05)), 0, 0, 180, (60, 60, 140), 3
    )
    return np.clip(frame + _GRAIN[i % 4], 0, 255).astype(np.uint8)


class PacedCamera:
    """30 fps of drawn frames, in real time; `dead`: nothing ever arrives."""

    def __init__(self, dead: bool = False) -> None:
        self.dead = dead
        self.label = "Fake laptop camera (OV02E10)"
        self.stopped = False
        self.t0 = time.perf_counter()

    def start(self) -> None:
        self.t0 = time.perf_counter()

    def stop(self) -> None:
        self.stopped = True

    def wait_frame(self, after: int, timeout: float = 0.5):
        if self.dead or self.stopped:
            time.sleep(timeout)
            return None
        n = after + 1
        due = self.t0 + n / 30
        wait = due - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        return n, due, draw(n)


class Detector:
    def detect(self, image):
        # the frame number isn't passed in: find the drawn face instead (the only
        # skin-coloured blob; shrinking averages the grain away), so the box matches the picture
        small = cv2.resize(image, (320, 180), interpolation=cv2.INTER_AREA)
        pts = cv2.findNonZero(cv2.inRange(small, (132, 162, 202), (168, 198, 238)))
        if pts is None:
            return []
        x, y, w, h = (4 * v for v in cv2.boundingRect(pts))
        return [face_det((x + w / 2) / 1280, (y + h / 2) / 720, float(w))]


class PacedMic:
    """A second of quiet, then speech (quiet at first), in real time."""

    def __init__(self) -> None:
        self.label = "Fake laptop mic (Microphone Array)"
        self.detail = ""
        quiet = np.zeros(16000, np.float32)
        soft = speech_like(1.5, level=0.004, seed=2)
        good = speech_like(8.0, level=0.08, seed=3)
        self.samples = np.concatenate([quiet, soft, good, np.zeros(16000 * 30, np.float32)])
        self.i = 0
        self.t0 = 0.0
        self.stopped = False

    def start(self) -> None:
        self.t0 = time.perf_counter()
        self.i = 0

    def stop(self) -> None:
        self.stopped = True

    def read(self, timeout: float = 0.1):
        if self.stopped:
            return None
        due = self.t0 + self.i / 16000
        wait = due - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        block = self.samples[self.i : self.i + 160]
        t = self.t0 + self.i / 16000
        self.i += 160
        return t, block


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--data", help="folder for the fake prints (default: a new temp folder)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    data = Path(args.data) if args.data else Path(tempfile.mkdtemp(prefix="attune_station_e2e_"))
    data.mkdir(parents=True, exist_ok=True)
    config = {
        "engine": {"data_dir": str(data)},
        "vision": {"camera_name": "Brio 101", "enroll_crops": 8, "enroll_min_crops": 5},
        "audio": {"device_name": "Brio 101"},
        "voice": {"enroll_s": 3.0},
        "save": {"consent_timeout_s": 60},
        "pages": {
            "frame_width": 1280,
            "frame_height": 720,
            "jpeg_quality": 70,
            "bubble_chars": 42,
            "bubble_lines": 2,
            "bubble_fade_s": 4,
        },
        "speech_out": {"presets": ["Nice to meet you"]},
        "enroll": {
            "source": "station",
            "face_s": 2.0,
            # generous limits: on a busy laptop the drawn frames come slower (P-37)
            "face_timeout_s": 45,
            "open_timeout_s": 5.0,
            "voice_timeout_s": 45,
            "decision_timeout_s": 60,
        },
    }
    bus = Bus()
    session_log = SessionLog(bus, data / "sessions", "e2e")
    router = CommandRouter(bus, session_log)
    router.connect()
    flow = SaveFlow(bus, config)
    flow.start()
    hub = Hub(bus, config, "e2e", router, data_dir=data)
    hub.connect()
    saved: dict[str, int] = {}

    def save_face(req: dict) -> dict:
        slug = "".join(ch for ch in req["name"].lower() if ch.isalnum()) or "person"
        saved[slug] = saved.get(slug, 0) + 1
        pid = f"{slug}-e2e{saved[slug]}"
        folder = data / "people" / pid
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"name": req["name"], "consent_t": req["consent_t"]}
        (folder / "meta.json").write_text(json.dumps(meta))
        bus.publish(
            C.ENROLL_RESULT,
            C.EnrollResult(
                pid, "face", True, "", req.get("track_id"), "station", req["session_id"]
            ),
        )
        bus.publish(C.PERSON_CHANGED, C.PersonChanged(pid, req["name"], "enrolled"))
        return {"person_id": pid}

    current = {"name": ""}

    def camera():
        return PacedCamera(dead=current["name"].lower().startswith("nocam"))

    station = StationEnroller(
        bus,
        config,
        Detector(),
        FakeEmbedder(SAM, noise=0.03),
        VisionHooks(lambda: ("Brio 101", True), lambda tap: None, save_face),
        root=str(data),
        camera_factory=camera,
        mic_factory=PacedMic,
        extractor=lambda audio: np.eye(1, 192, dtype=np.float32)[0],
        vad_factory=lambda: energy_vad,
    )

    def on_command(ev) -> None:  # the vision thread's part: glasses prints for the check
        if ev.get("name") != "enroll.station":
            return
        args = dict(ev.get("args") or {})
        glasses = None
        if args.get("action", "start") == "start":
            current["name"] = str(args.get("name") or "")
            tid = args.get("track_id")
            if tid in GLASSES:
                rng = np.random.default_rng(int(tid))
                who = GLASSES[tid][1]
                rows = [who + 0.03 * rng.standard_normal(who.shape) for _ in range(6)]
                glasses = np.stack([r / np.linalg.norm(r) for r in rows]).astype(np.float32)
        station.command(args, glasses)

    bus.subscribe(C.COMMAND, on_command)
    stop = threading.Event()

    def glasses_scene() -> None:  # Sam and Ana in front of the glasses, both named
        n = 0
        while not stop.is_set():
            n += 1
            faces = [
                C.FaceState(7, [300, 200, 220, 220], "Sam", "named", 0.0, False, False),
                C.FaceState(8, [800, 220, 180, 180], "Ana", "named", 0.0, False, False),
            ]
            bus.publish(C.SCENE, C.Scene(n, time.perf_counter(), faces, [], False))
            stop.wait(0.3)

    threading.Thread(target=glasses_scene, daemon=True).start()
    hub.start()
    app = create_app(hub, data_root=data)
    web = WebServer(app, "127.0.0.1", args.port)
    web.start()
    print(f"READY {web.url}", flush=True)
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    try:
        while not stop.is_set():
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        station.stop()
        flow.stop()
        web.stop()
        hub.stop()
        shutil.rmtree(data, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
