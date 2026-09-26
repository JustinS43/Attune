"""Developer viewer for Section 1: see faces, names and lip scores in an OpenCV window.

Until Section 4's lens view exists, this is how to check Vision by eye:

    uv run --project engine python -m attune.vision.devview
    uv run --project engine python -m attune.vision.devview --source clip.mp4
    uv run --project engine python -m attune.vision.devview --list-cameras

Keys: E enroll the biggest face (asks for a name in the terminal, you confirm
consent there), F forget session, P pause, Q quit.

It uses a tiny in-process bus; the real one is Section 4's core/bus.py.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import logging
import threading
import tomllib

import cv2

from ..fusion.service import FusionService
from . import types as T
from .camera import list_cameras
from .service import VisionService

log = logging.getLogger(__name__)


class SimpleBus:
    def __init__(self):
        self._subs = collections.defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic, callback):
        with self._lock:
            self._subs[topic].append(callback)

    def publish(self, topic, event):
        with self._lock:
            subs = list(self._subs[topic])
        for cb in subs:
            cb(event)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", default="config/attune.toml")
    ap.add_argument("--source", help="video file or camera index instead of the named webcam")
    ap.add_argument("--list-cameras", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

    if args.list_cameras:
        for cam in list_cameras():
            print(f"{cam.index}: {cam.name}")
        return
    config = {}
    try:
        with open(args.config, "rb") as fh:
            config = tomllib.load(fh)
    except FileNotFoundError:
        log.info("No %s; using defaults", args.config)
    source = int(args.source) if args.source and args.source.isdigit() else args.source

    bus = SimpleBus()
    state = {"frame": None, "scene": None, "status": {}, "msg": ""}
    bus.subscribe(T.VISION_FRAME, lambda ev: state.__setitem__("frame", ev))
    bus.subscribe(T.SCENE, lambda ev: state.__setitem__("scene", ev))
    bus.subscribe(T.STATUS_PART, lambda ev: state["status"].__setitem__(ev.part, ev))
    bus.subscribe(
        T.ENROLL_RESULT,
        lambda ev: state.__setitem__("msg", f"enroll: {'ok' if ev.ok else ev.reason}"),
    )
    vision = VisionService(bus, config, source=source)
    fusion = FusionService(bus, config)
    vision.start()
    fusion.start()
    paused = False
    try:
        while True:
            frame, scene = state["frame"], state["scene"]
            if frame is not None:
                img = frame.image.copy()
                for f in scene.faces if scene else []:
                    x, y, w, h = (int(v) for v in f.box)
                    color = (80, 220, 80) if f.status in ("named", "enrolled") else (0, 200, 255)
                    cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
                    cv2.putText(
                        img,
                        f"{f.label}  lips {f.lip_score:.3f}",
                        (x, max(y - 8, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        color,
                        2,
                    )
                vis = state["status"].get("vision")
                if vis:
                    m = vis.metrics
                    line = f"{m.get('camera')}  cam {m.get('camera_fps')} fps  vision {m.get('vision_fps')} fps  det {m.get('det_ms')} ms  gpu {m.get('gpu')}"
                    cv2.putText(
                        img, line, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2
                    )
                if state["msg"]:
                    cv2.putText(
                        img, state["msg"], (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2
                    )
                scale = 1280 / img.shape[1]
                cv2.imshow("Attune vision (dev)", cv2.resize(img, None, fx=scale, fy=scale))
            key = cv2.waitKey(30) & 0xFF
            if key == ord("q"):
                break
            if key == ord("f"):
                bus.publish(T.SESSION_FORGET, {})
            if key == ord("p"):
                paused = not paused
                bus.publish(T.PAUSED, {"paused": paused})
            if key == ord("e") and scene and scene.faces:
                biggest = max(scene.faces, key=lambda f: f.box[2])
                name = input("Name to enroll: ").strip()
                ok = (
                    input(
                        f"Does {name} consent to storing a face print on this laptop? [y/N] "
                    ).lower()
                    == "y"
                )
                bus.publish(
                    T.COMMAND,
                    {
                        "name": "enroll.start",
                        "args": {
                            "track_id": biggest.track_id,
                            "name": name,
                            "consent": ok,
                            "consent_t": datetime.datetime.now()
                            .astimezone()
                            .isoformat(timespec="seconds")
                            if ok
                            else None,
                        },
                    },
                )
                state["msg"] = "enrolling: turn your head a little for 5 s"
    finally:
        fusion.stop()
        vision.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
