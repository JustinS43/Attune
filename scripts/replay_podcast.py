"""Replay a recorded podcast run (eval_podcast.py) to the pages, as if the engine were live.

Section 4 - Pages, Engine & Demo. TODO: P-45.

    python scripts/replay_podcast.py data/podcasts/gmm2/fix2.json --start 30 --port 8021
    # then open http://127.0.0.1:8021/lens/?source=live  (any glasses mode, any page)

The run's clip plays as the camera and every recorded `scene`, `caption` and
`caption_retract` message is sent at the moment the engine sent it, through the engine's
own web server and WebSocket hub, so the lens lays out exactly what the engine produced.
No models run: the laptop's load can't change what is shown, so a layout can be
reviewed (and screenshotted) frame by frame. `--speed 0.5` plays at half speed, `--loop`
starts over at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

log = logging.getLogger("replay_podcast")
KINDS = ("scene", "caption", "caption_retract", "status")


def main(argv: list[str] | None = None) -> int:
    import cv2
    import uvicorn
    from attune.core.bus import Bus
    from attune.core.contracts import VISION_FRAME, Frame
    from attune.server.app import create_app
    from attune.server.ws import Hub

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="run JSON written by eval_podcast.py run")
    ap.add_argument("--video", help="the clip (default: clip.mp4 next to the run)")
    ap.add_argument("--start", type=float, default=0.0, help="clip second to start at")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--port", type=int, default=8021)
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    video = args.video or str(Path(args.run).with_name("clip.mp4"))
    t0 = float(run["t0"])
    msgs = sorted(
        ((float(t) - t0, m) for t, m in run["messages"] if m.get("type") in KINDS),
        key=lambda x: x[0],
    )
    bus = Bus()
    hub = Hub(bus, {"pages": {"frame_width": 1280, "frame_height": 720}}, "replay", None, time.perf_counter)
    hub.connect()
    app = create_app(hub, data_root=ROOT)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))

    def play() -> None:
        while hub.loop is None:
            time.sleep(0.1)
        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        while True:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(args.start * fps))
            wall0, k, n = time.perf_counter(), 0, 0
            while n < len(msgs) and msgs[n][0] < args.start:
                n += 1
            while True:
                clip_t = args.start + (time.perf_counter() - wall0) * args.speed
                while n < len(msgs) and msgs[n][0] <= clip_t:
                    kind, body = msgs[n][1]["type"], dict(msgs[n][1])
                    hub.loop.call_soon_threadsafe(hub.broadcast, kind, body)
                    n += 1
                if args.start + k / fps <= clip_t:
                    ok, image = cap.read()
                    if not ok:
                        break
                    bus.publish(VISION_FRAME, Frame(int(args.start * fps) + k, t0 + clip_t, image))
                    k += 1
                time.sleep(0.004)
            log.info("clip ended")
            if not args.loop:
                return
            hub.loop.call_soon_threadsafe(hub.broadcast, "session_reset", {})

    threading.Thread(target=play, daemon=True, name="replay").start()
    hub.start()
    print(f"open http://127.0.0.1:{args.port}/lens/?source=live")
    asyncio.run(server.serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
