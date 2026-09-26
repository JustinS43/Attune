"""Developer viewer: see faces, names, lips and (with --all) live captions in an OpenCV window.

Until Section 4's engine and lens page exist, this is how to try Attune by eye:

    uv run --project engine python -m attune.vision.devview            # vision + who's talking
    uv run --project engine python -m attune.vision.devview --all      # + mic captions, alerts, names
    uv run --project engine python -m attune.vision.devview --source clip.mp4
    uv run --project engine python -m attune.vision.devview --list-cameras

Keys: E enroll the biggest face (name and consent asked in the terminal),
Y / N answer "Is this <name>?", A acknowledge an alert, F forget session,
P pause, Q quit.

`--all` also starts Section 2's AudioService (mic, VAD, Nemotron captions,
voice prints), AlertService and LLMService (Ollama) on the same in-process bus
and clock. It uses a tiny bus; the real one is Section 4's core/bus.py.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import logging
import threading
import time
import tomllib

import cv2
import numpy as np

from ..fusion.service import FusionService
from . import types as T
from .camera import list_cameras
from .service import VisionService

log = logging.getLogger(__name__)
WHITE, GREY, YELLOW, RED = (255, 255, 255), (170, 170, 170), (0, 230, 255), (60, 60, 255)


class SimpleBus:
    def __init__(self):
        self._subs = collections.defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic, callback):
        with self._lock:
            self._subs[topic].append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._subs[topic]:
                    self._subs[topic].remove(callback)

        return unsubscribe

    def publish(self, topic, event):
        with self._lock:
            subs = list(self._subs[topic])
        for cb in subs:
            try:
                cb(event)
            except Exception:
                log.exception("Subscriber to %s failed", topic)


class Board:
    """What the window shows, filled in from bus events (any thread)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.frame = self.scene = None
        self.status: dict = {}
        self.msg, self.msg_t = "", 0.0
        self.captions: collections.OrderedDict = collections.OrderedDict()
        self.translations: dict = {}
        self.proposal = None
        self.alert = None

    def note(self, text: str) -> None:
        log.info("NOTE %s", text)
        self.msg, self.msg_t = text, time.monotonic()

    def on_caption(self, ev) -> None:
        with self.lock:
            spk = T.get(ev, "speaker")
            label = T.get(spk, "label", "") or T.get(spk, "kind", "")
            self.captions[T.get(ev, "utt_id")] = (label, T.get(ev, "text"), T.get(ev, "final"))
            if T.get(ev, "final"):
                log.info("CAPTION %s: %s", label or "?", T.get(ev, "text"))
            self.captions.move_to_end(T.get(ev, "utt_id"))
            while len(self.captions) > 4:
                self.captions.popitem(last=False)

    def on_translation(self, ev) -> None:
        log.info("TRANSLATION %s", T.get(ev, "text_en"))
        with self.lock:
            self.translations[T.get(ev, "utt_id")] = T.get(ev, "text_en")

    def on_proposal(self, ev) -> None:
        with self.lock:
            state = T.get(ev, "state")
            log.info("NAME %s for track %s: %s", T.get(ev, "name"), T.get(ev, "track_id"), state)
            if state == "proposed":
                self.proposal = ev
            elif self.proposal and T.get(self.proposal, "proposal_id") == T.get(ev, "proposal_id"):
                self.proposal = None
                self.note(f"name {T.get(ev, 'name')}: {state}")

    def on_alert(self, ev) -> None:
        with self.lock:
            log.info("ALERT %s %s (%s)", T.get(ev, "kind"), T.get(ev, "state"), T.get(ev, "side"))
            self.alert = None if T.get(ev, "state") in ("clear", "acknowledged") else ev
            if self.alert is None:
                self.note(f"alert {T.get(ev, 'kind')}: {T.get(ev, 'state')}")


def _text(img, s, xy, color=WHITE, scale=0.7, thick=2):
    cv2.putText(img, s, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3)
    cv2.putText(img, s, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick)


def draw(board: Board, audio_on: bool):
    with board.lock:
        frame, scene = board.frame, board.scene
        captions = list(board.captions.items())
        proposal, alert = board.proposal, board.alert
    if frame is None:
        return None
    img = frame.image.copy()
    for f in scene.faces if scene else []:
        x, y, w, h = (int(v) for v in f.box)
        color = (80, 220, 80) if f.status in ("named", "enrolled") else (0, 200, 255)
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 4 if f.is_speaker else 2)
        tag = f"{f.label}  lips {f.lip_score:.3f}" + ("  SPEAKING" if f.is_speaker else "")
        _text(img, tag, (x, max(y - 8, 20)), color)
    if scene:
        for o in scene.offscreen:
            _text(
                img,
                f"{'<' if o.side == 'left' else '>'} {o.label}",
                (20 if o.side == "left" else img.shape[1] - 400, img.shape[0] // 2),
                YELLOW,
                1.0,
            )
    y = 32
    for part, st in sorted(board.status.items()):
        m = T.get(st, "metrics", {}) or {}
        ok = T.get(st, "ok", True)
        if part == "vision":
            line = f"vision: {m.get('camera')} {m.get('camera_fps')} fps, {m.get('vision_fps')} fps processed, det {m.get('det_ms')} ms, gpu {m.get('gpu')}"
        else:
            line = f"{part}: {T.get(st, 'detail', '')}"
        _text(img, line, (10, y), WHITE if ok else RED, 0.6)
        y += 26
    if board.msg and time.monotonic() - board.msg_t < 6:
        _text(img, board.msg, (10, y + 6), YELLOW)
    if alert:
        _text(
            img,
            f"ALERT: {T.get(alert, 'kind')} ({T.get(alert, 'side')})  press A to acknowledge",
            (10, img.shape[0] // 3),
            RED,
            1.4,
            3,
        )
    if proposal:
        _text(
            img,
            f"Is this {T.get(proposal, 'name')}?  Y / N",
            (10, img.shape[0] // 3 + 60),
            YELLOW,
            1.2,
            3,
        )
    y = img.shape[0] - 30 * len(captions) - 20
    for utt, (label, text, final) in captions:
        s = f"{label}: {text}" if label else text
        _text(img, s[-110:], (20, y), WHITE if final else GREY, 0.9)
        tr = board.translations.get(utt)
        if tr:
            y += 30
            _text(img, f"   ({tr[-100:]})", (20, y), GREY, 0.8)
        y += 30
    if audio_on and not captions:
        _text(img, "(listening...)", (20, img.shape[0] - 30), GREY, 0.8)
    return img


class FilePlayer:
    """Feed a WAV file onto the bus as `audio.block` (16 and 32 kHz) in real time, like the mic.

    Laptop mic arrays cancel their own speakers' output, so a clip played aloud never
    reaches the mic; this is how to try captions with a recording.
    """

    def __init__(self, bus, path: str, clock, delay_s: float = 5.0, repeat_s: float = 0.0):
        import wave

        import soxr

        with wave.open(path, "rb") as w:
            width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError("expected a 16-bit WAV file")
        pcm = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
        pcm = pcm.reshape(-1, channels).mean(axis=1)
        self.streams = {r: soxr.resample(pcm, rate, r).astype(np.float32) for r in (16000, 32000)}
        self.bus, self.clock, self.delay_s, self.repeat_s = bus, clock, delay_s, repeat_s
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="file-audio", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1.0)

    def _run(self) -> None:
        if self.stop_event.wait(self.delay_s):
            return
        while not self.stop_event.is_set():
            t0 = self.clock()
            n16 = len(self.streams[16000])
            for i in range(0, n16, 160):  # 10 ms blocks on the shared clock
                t = t0 + i / 16000
                if self.stop_event.wait(max(0.0, t - self.clock())):
                    return
                for rate, data in self.streams.items():
                    a, b = i * rate // 16000, min(len(data), (i + 160) * rate // 16000)
                    if b > a:
                        self.bus.publish(
                            T.AUDIO_BLOCK, {"t": t, "sample_rate": rate, "samples": data[a:b]}
                        )
            log.info("Finished playing the audio file into the pipeline")
            if not self.repeat_s or self.stop_event.wait(self.repeat_s):
                return


def _start_audio(bus, config, use_mic: bool = True) -> list:
    """Start Section 2's services; each one that can't start is logged and skipped."""
    started = []
    try:
        from ..alerts.service import AlertService
        from ..audio.service import AudioService
        from ..llm.service import LLMService
    except Exception:
        log.exception("Audio packages missing: uv sync --project engine --extra audio")
        return started
    for name, cls in (("audio", AudioService), ("alerts", AlertService), ("llm", LLMService)):
        try:
            svc = (
                cls(bus, config, mic=None if use_mic else False)
                if name == "audio"
                else cls(bus, config)
            )
            svc.start()
            started.append(svc)
            log.info("Started %s", name)
        except Exception:
            log.exception("Could not start %s; continuing without it", name)
    return started


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", default="config/attune.toml")
    ap.add_argument("--source", help="video file or camera index instead of the named webcam")
    ap.add_argument("--all", action="store_true", help="also run mic captions, alerts and names")
    ap.add_argument("--seconds", type=float, help="quit by itself after this long")
    ap.add_argument(
        "--audio-file", help="with --all: feed this 16-bit WAV instead of the mic (5 s after start)"
    )
    ap.add_argument("--repeat", type=float, default=0.0, help="replay --audio-file after N s")
    ap.add_argument("--list-cameras", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

    if args.list_cameras:
        for cam in list_cameras():
            print(f"{cam.index}: {cam.name}")
        return
    config: dict = {}
    try:
        with open(args.config, "rb") as fh:
            config = tomllib.load(fh)
    except FileNotFoundError:
        log.info("No %s; using defaults", args.config)
        if args.all:
            raise SystemExit(
                "--all needs a config: copy config/attune.example.toml to config/attune.toml"
            )
    source = int(args.source) if args.source and args.source.isdigit() else args.source

    bus = SimpleBus()
    board = Board()
    bus.subscribe(T.VISION_FRAME, lambda ev: setattr(board, "frame", ev))
    bus.subscribe(T.SCENE, lambda ev: setattr(board, "scene", ev))
    bus.subscribe(T.STATUS_PART, lambda ev: board.status.__setitem__(T.get(ev, "part"), ev))
    bus.subscribe(
        T.ENROLL_RESULT,
        lambda ev: board.note(
            f"enroll {T.get(ev, 'part')}: {'ok' if T.get(ev, 'ok') else T.get(ev, 'reason')}"
        ),
    )
    bus.subscribe(T.CAPTION, board.on_caption)
    bus.subscribe("caption.translation", board.on_translation)
    bus.subscribe(T.NAME_PROPOSAL, board.on_proposal)
    bus.subscribe("alert", board.on_alert)
    bus.subscribe(
        T.VISION_DESCRIPTION,
        lambda ev: log.info("DESCRIPTION track %s: %s", T.get(ev, "track_id"), T.get(ev, "label")),
    )

    # One clock for everyone: vision and fusion stamp with perf_counter, so audio must too.
    config["clock"] = time.perf_counter
    vision = VisionService(bus, config, source=source)
    fusion = FusionService(bus, config)
    vision.start()
    fusion.start()
    others = _start_audio(bus, config, use_mic=not args.audio_file) if args.all else []
    if args.all and args.audio_file:
        player = FilePlayer(bus, args.audio_file, config["clock"], repeat_s=args.repeat)
        player.start()
        others.append(player)
    paused = False
    end = time.monotonic() + args.seconds if args.seconds else None
    try:
        while end is None or time.monotonic() < end:
            img = draw(board, bool(others))
            if img is not None:
                scale = 1280 / img.shape[1]
                cv2.imshow("Attune (dev)", cv2.resize(img, None, fx=scale, fy=scale))
            key = cv2.waitKey(30) & 0xFF
            if key == ord("q"):
                break
            if key == ord("f"):
                bus.publish(T.SESSION_FORGET, {})
                board.note("session forgotten")
            if key == ord("p"):
                paused = not paused
                bus.publish(T.PAUSED, {"paused": paused})
                board.note("paused" if paused else "resumed")
            if key in (ord("y"), ord("n")) and board.proposal:
                pid = T.get(board.proposal, "proposal_id")
                bus.publish(
                    T.COMMAND,
                    {
                        "name": "name.answer",
                        "args": {"proposal_id": pid, "accept": key == ord("y")},
                    },
                )
            if key == ord("a") and board.alert:
                bus.publish(
                    T.COMMAND,
                    {"name": "alert.ack", "args": {"alert_id": T.get(board.alert, "alert_id")}},
                )
            if key == ord("e") and board.scene and board.scene.faces:
                biggest = max(board.scene.faces, key=lambda f: f.box[2])
                name = input("Name to enroll: ").strip()
                what = "a face print" + (" and a voice print" if others else "")
                ok = (
                    input(f"Does {name} consent to storing {what} on this laptop? [y/N] ").lower()
                    == "y"
                )
                consent_t = (
                    datetime.datetime.now().astimezone().isoformat(timespec="seconds")
                    if ok
                    else None
                )
                bus.publish(
                    T.COMMAND,
                    {
                        "name": "enroll.start",
                        "args": {
                            "track_id": biggest.track_id,
                            "name": name,
                            "consent": ok,
                            "consent_t": consent_t,
                        },
                    },
                )
                board.note(
                    "enrolling: turn your head a little for 5 s"
                    + (", then keep talking for the voice" if others else "")
                )
    finally:
        for svc in reversed(others):
            svc.stop()
        fusion.stop()
        vision.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
