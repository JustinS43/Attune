"""WebSocket hub: bus events to the pages, page commands to the bus.

Section 4 - Pages, Engine & Demo. TODO: P-03. Contracts: docs/contracts.md (3, 4 and
"Pages integration additions").

Threads and the event loop:
- Bus callbacks run on the publishers' threads. They only store a reference or hand
  the event to the asyncio loop with `call_soon_threadsafe`, so they return at once.
- Frames are JPEG-encoded on the hub's own `frame-encoder` thread, once per camera
  frame and only while some page asked for frames (at most `pages.max_fps`/s).
- Each page has a sender task with an unbounded JSON queue and a one-slot frame
  buffer: a slow page skips frames but never loses other messages.
- Page commands go to a single dispatcher thread, in order, so a slow bus
  subscriber can't stall the loop.

Every JSON message is `{"type": ..., "seq": n, ...}` with `seq` counting up per page.
Binary frames: LE uint64 frame_no, LE float64 capture t, then a JPEG (1280x720).

Enrollment station (V-23): `enroll.station` commands get the sending page's `client_id`, and
`enroll_state`, `enroll_preview`, `enroll_level` and `enroll_mismatch` go only to that page
(if it reconnects, the next phone to say hello takes the save over). Previews use a one-slot
buffer like frames, so a slow phone skips them instead of queueing.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import itertools
import json
import logging
import math
import struct
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import numpy as np
from starlette.websockets import WebSocket

from ..core import contracts as C
from ..core.contracts import get

log = logging.getLogger(__name__)

MAX_QUEUE = 5000  # JSON messages waiting for one page before it is disconnected
CAPTION_MEMORY = 200


class _Drop:
    pass


_DROP = _Drop()


def to_jsonable(obj: Any) -> Any:
    """Dataclasses, dicts, tuples and numpy scalars to plain JSON values.

    Numpy arrays and bytes (frames, crops, audio) are dropped; NaN and inf become None.
    """
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (np.ndarray, bytes, bytearray, memoryview)):
        return _DROP
    if isinstance(obj, np.generic):
        return to_jsonable(obj.item())
    if is_dataclass(obj) and not isinstance(obj, type):
        pairs = ((f.name, getattr(obj, f.name)) for f in fields(obj))
    elif isinstance(obj, Mapping):
        pairs = obj.items()
    elif isinstance(obj, (list, tuple, set, frozenset, deque)):
        out = [to_jsonable(v) for v in obj]
        return [v for v in out if v is not _DROP]
    else:
        return str(obj)
    result = {}
    for key, value in pairs:
        value = to_jsonable(value)
        if value is not _DROP:
            result[str(key)] = value
    return result


def scale_box(box: Any, sx: float, sy: float) -> list[float]:
    x, y, w, h = (float(v) for v in box)
    return [round(x * sx, 1), round(y * sy, 1), round(w * sx, 1), round(h * sy, 1)]


def load_people(people_dir: Path) -> list[dict[str, Any]]:
    """Enrolled people from data/people/<id>/meta.json; never reads the prints themselves."""
    people = []
    if not people_dir.is_dir():
        return people
    for folder in sorted(people_dir.iterdir()):
        meta_path = folder / "meta.json"
        if not folder.is_dir() or not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("Unreadable %s", meta_path)
            continue
        people.append(
            {
                "person_id": folder.name,
                "name": meta.get("name", folder.name),
                "consent_t": meta.get("consent_t"),
                "has_face": (folder / "face.npy").is_file(),
                "has_voice": any(p.name.startswith("voice.") for p in folder.iterdir()),
            }
        )
    return people


class Client:
    """One connected page (used only on the event loop)."""

    def __init__(self, ws: Any, cid: int) -> None:
        self.ws = ws
        self.id = cid
        self.role: str | None = None
        self.frames = False
        self.seq = 0
        self.queue: deque[str] = deque()
        self.frame: bytes | None = None
        self.preview: str | None = None  # the newest enroll_preview (V-23), droppable
        self.wake = asyncio.Event()
        self.frames_sent = 0
        self.frames_skipped = 0
        self.closed = False

    def push(self, msg_type: str, body: dict[str, Any]) -> None:
        if self.closed:
            return
        self.seq += 1
        body = {k: v for k, v in body.items() if k not in ("type", "seq")}
        self.queue.append(json.dumps({"type": msg_type, "seq": self.seq, **body}))
        self.wake.set()

    def push_preview(self, body: dict[str, Any]) -> None:
        """Enrollment station preview: only the newest one waits to be sent."""
        if self.closed:
            return
        self.seq += 1
        self.preview = json.dumps({"type": C.WS_ENROLL_PREVIEW, "seq": self.seq, **body})
        self.wake.set()

    def push_frame(self, data: bytes) -> None:
        if self.closed:
            return
        if self.frame is not None:
            self.frames_skipped += 1
        self.frame = data
        self.wake.set()


class Hub:
    """Bridges the bus and the pages' WebSockets."""

    def __init__(
        self,
        bus,
        config: dict[str, Any] | None = None,
        session_id: str = "",
        router=None,
        clock: Callable[[], float] = time.perf_counter,
        data_dir: str | Path | None = None,
    ) -> None:
        config = config or {}
        pages = config.get("pages") or {}
        vision = config.get("vision") or {}
        engine = config.get("engine") or {}
        self.bus, self.router, self.clock = bus, router, clock
        self.session_id = session_id
        self.frame_w = int(pages.get("frame_width", 1280))
        self.frame_h = int(pages.get("frame_height", 720))
        self.jpeg_quality = int(pages.get("jpeg_quality", 80))
        self.max_fps = float(pages.get("max_fps", 30))
        self.thumb_every_s = float(pages.get("thumbnail_every_s", 1.0))
        self.cam_w = int(vision.get("width", 1920))
        self.cam_h = int(vision.get("height", 1080))
        self.welcome_config = {
            "bubble_chars": pages.get("bubble_chars", 42),
            "bubble_lines": pages.get("bubble_lines", 2),
            "bubble_fade_s": pages.get("bubble_fade_s", 4),
            "presets": list((config.get("speech_out") or {}).get("presets", [])),
        }
        enroll = config.get("enroll")
        if isinstance(enroll, dict):
            # V-23: where people are saved; the phone picks its screens from this
            self.welcome_config["enroll"] = {
                "source": enroll.get("source", "station"),
                "sentence": enroll.get("sentence", ""),
            }
        data = Path(data_dir) if data_dir else Path(engine.get("data_dir", "data"))
        self.people_dir = data / "people"

        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: dict[int, Client] = {}
        self._ids = itertools.count(1)
        self._paused = False
        self.captions: OrderedDict[str, dict] = OrderedDict()
        self.translations: OrderedDict[str, str] = OrderedDict()
        self.event_log: deque[dict] = deque(maxlen=50)
        self.people: list[dict] = []
        self.latest_status: dict | None = None
        self._part_ok: dict[str, bool] = {}
        self._hw_connected: bool | None = None
        self._hw_link: Any = None  # latest hw.link, sent to pages that connect later
        self.save_pending: dict | None = None  # the save request waiting for consent (P-29)
        self.station_session: str | None = None  # the station save in progress (V-23)
        self.station_client: int | None = None  # the page following it
        self.station_state: dict | None = None  # its latest enroll_state

        # shared with the encoder thread
        self._frame: Any = None
        self._scene_raw: Any = None
        self._frame_event = threading.Event()
        self._frame_clients = 0
        self._console_clients = 0
        self._stop = threading.Event()
        self._encoder: threading.Thread | None = None
        self._commands = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="commands")
        self._unsubs: list[Callable[[], None]] = []
        self.frames_encoded = 0
        self.encode_ms: float | None = None  # moving average, written by the encoder only
        self._health_mark = (time.monotonic(), 0)

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> None:
        """Subscribe to the bus. Call before the other services start."""
        post = self._post
        subs: dict[str, Callable[[Any], None]] = {
            C.VISION_FRAME: self._on_frame_bus,
            C.SCENE: self._on_scene_bus,
            C.CAPTION: lambda ev: post(self._on_caption, ev),
            C.CAPTION_RETRACT: lambda ev: post(self._on_caption_retract, ev),
            C.CAPTION_TRANSLATION: lambda ev: post(self._on_translation, ev),
            C.NAME_PROPOSAL: lambda ev: post(self._on_relay, C.WS_NAME_PROPOSAL, ev),
            C.ALERT: lambda ev: post(self._on_alert, ev),
            C.REPLY_SUGGESTIONS: lambda ev: post(self._on_relay, C.WS_REPLY_SUGGESTIONS, ev),
            C.REPLY_SPOKEN: lambda ev: post(self._on_reply_spoken, ev),
            C.PAUSED: self._on_paused_bus,
            C.CAMERA_STATE: lambda ev: post(self._on_camera, ev),
            C.ENROLL_RESULT: lambda ev: post(self._on_enroll_result, ev),
            C.ENROLL_PROGRESS: lambda ev: post(self._on_relay, C.WS_ENROLL_PROGRESS, ev),
            C.SAVE_REQUEST: lambda ev: post(self._on_save, C.WS_SAVE_REQUEST, ev),
            C.SAVE_CANCEL: lambda ev: post(self._on_save, C.WS_SAVE_CANCEL, ev),
            C.PERSON_CHANGED: lambda ev: post(self._on_person_changed, ev),
            C.HW_LINK: self._on_hw_link_bus,
            C.STATUS: lambda ev: post(self._on_status, ev),
            C.STATUS_PART: lambda ev: post(self._on_status_part, ev),
            C.SESSION_FORGET: lambda ev: post(self._on_forget, ev),
            C.COMMAND: lambda ev: post(self._on_command_event, ev),
            C.ENROLL_STATE: lambda ev: post(self._on_station, C.ENROLL_STATE, ev),
            C.ENROLL_PREVIEW: lambda ev: post(self._on_station, C.ENROLL_PREVIEW, ev),
            C.ENROLL_LEVEL: lambda ev: post(self._on_station, C.ENROLL_LEVEL, ev),
            C.ENROLL_MISMATCH: lambda ev: post(self._on_station, C.ENROLL_MISMATCH, ev),
        }
        self._unsubs = [self.bus.subscribe(topic, cb) for topic, cb in subs.items()]

    def start(self) -> None:
        """Start the frame encoder thread (the web server runs the loop)."""
        if not self._unsubs:
            self.connect()
        self._stop.clear()
        self._encoder = threading.Thread(
            target=self._encode_loop, name="frame-encoder", daemon=True
        )
        self._encoder.start()

    def stop(self) -> None:
        self._stop.set()
        self._frame_event.set()
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self._encoder:
            self._encoder.join(timeout=1.0)
        self._commands.shutdown(wait=False, cancel_futures=True)

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called by the web app on startup, on the loop that serves the WebSockets."""
        self.loop = loop
        self.people = load_people(self.people_dir)

    def detach(self) -> None:
        self.loop = None
        for client in list(self.clients.values()):
            client.closed = True
            client.wake.set()

    @property
    def camera_on(self) -> bool:
        return bool(self.router.camera_on) if self.router is not None else True

    @property
    def paused(self) -> bool:
        if self.router is not None:
            return bool(self.router.paused)
        return self._paused

    # ------------------------------------------------------------------ bus side
    def _post(self, fn: Callable, *args: Any) -> None:
        loop = self.loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # loop closed during shutdown
            pass

    def _on_frame_bus(self, ev: Any) -> None:
        self._frame = ev
        self._frame_event.set()

    def _on_scene_bus(self, ev: Any) -> None:
        self._scene_raw = ev
        self._post(self._on_scene, ev)

    def _on_hw_link_bus(self, ev: Any) -> None:
        self._hw_link = ev
        self._post(self._on_hw_link, ev)

    def _on_paused_bus(self, ev: Any) -> None:
        self._paused = bool(get(ev, "paused", False))
        self._post(self._on_paused, ev)

    # ------------------------------------------------------------------ loop side
    def broadcast(self, msg_type: str, body: dict[str, Any]) -> None:
        roles = C.WS_AUDIENCE.get(msg_type, frozenset(C.ROLES))
        for client in list(self.clients.values()):
            if client.role in roles:
                self._push(client, msg_type, body)

    def _push(self, client: Client, msg_type: str, body: dict[str, Any]) -> None:
        client.push(msg_type, body)
        if len(client.queue) > MAX_QUEUE:
            log.warning("Page %s is not reading its messages; disconnecting it", client.id)
            client.closed = True
            client.queue.clear()
            client.wake.set()

    def _log_event(self, text: str) -> None:
        entry = {"t": round(self.clock(), 3), "text": text}
        self.event_log.append(entry)
        self.broadcast(C.WS_EVENT_LOG, entry)

    def _camera_size(self) -> tuple[int, int]:
        image = get(self._frame, "image")
        shape = getattr(image, "shape", None)
        if shape is not None and len(shape) >= 2:
            return int(shape[1]), int(shape[0])
        return self.cam_w, self.cam_h

    def scene_message(self, ev: Any) -> dict[str, Any]:
        body = to_jsonable(ev)
        cw, ch = self._camera_size()
        sx, sy = self.frame_w / cw, self.frame_h / ch
        for face in body.get("faces") or []:
            if isinstance(face.get("box"), list) and len(face["box"]) == 4:
                face["box"] = scale_box(face["box"], sx, sy)
        return body

    def _on_scene(self, ev: Any) -> None:
        self.broadcast(C.WS_SCENE, self.scene_message(ev))

    def _on_caption(self, ev: Any) -> None:
        body = to_jsonable(ev)
        utt = str(body.get("utt_id"))
        msg = {
            "utt_id": body.get("utt_id"),
            "speaker": body.get("speaker"),
            "text": body.get("text", ""),
            "final": bool(body.get("final", False)),
            "lang": body.get("lang"),
            "words": body.get("words") or [],
        }
        for key, value in body.items():  # keep fields added to the contract later
            msg.setdefault(key, value)
        if utt in self.translations:
            msg["translation"] = self.translations[utt]
        self.captions[utt] = msg
        self.captions.move_to_end(utt)
        while len(self.captions) > CAPTION_MEMORY:
            self.captions.popitem(last=False)
        self.broadcast(C.WS_CAPTION, msg)

    def _on_caption_retract(self, ev: Any) -> None:
        """A caption segment left its utterance: forget it and tell the pages to drop it."""
        utt = get(ev, "utt_id")
        if utt is None:
            return
        self.captions.pop(str(utt), None)
        self.translations.pop(str(utt), None)
        self.broadcast(C.WS_CAPTION_RETRACT, {"utt_id": utt})

    def _on_translation(self, ev: Any) -> None:
        utt, text = str(get(ev, "utt_id")), get(ev, "text_en")
        if not text:
            return
        self.translations[utt] = text
        while len(self.translations) > CAPTION_MEMORY:
            self.translations.popitem(last=False)
        caption = self.captions.get(utt)
        if caption is not None:
            caption = {**caption, "translation": text}
            self.captions[utt] = caption
            self.broadcast(C.WS_CAPTION, caption)

    def _on_relay(self, msg_type: str, ev: Any) -> None:
        body = to_jsonable(ev)
        self.broadcast(msg_type, body if isinstance(body, dict) else {"value": body})
        if msg_type == C.WS_NAME_PROPOSAL:
            self._log_event(f"Name {body.get('name')}: {body.get('state')}")

    def _on_save(self, msg_type: str, ev: Any) -> None:
        """Save a person (P-29): the consent request and its end go to every page."""
        body = to_jsonable(ev)
        self.broadcast(msg_type, body)
        if msg_type == C.WS_SAVE_REQUEST:
            self.save_pending = body
            self._log_event(f"Save {body.get('name')}? Waiting for their consent")
        else:
            if self.save_pending and self.save_pending.get("request_id") == body.get("request_id"):
                self.save_pending = None
            self._log_event(f"Save {body.get('name') or 'request'}: {body.get('reason')}")

    def _on_station(self, topic: str, ev: Any) -> None:
        """Enrollment station messages, only for the page that started the save (V-23)."""
        preview = topic == C.ENROLL_PREVIEW
        jpeg = get(ev, "jpeg") if preview else None
        body = to_jsonable(ev)
        if preview:
            if not isinstance(jpeg, (bytes, bytearray)):
                return
            body["jpeg_b64"] = base64.b64encode(bytes(jpeg)).decode("ascii")
        cid = body.pop("client_id", None)
        sid = body.get("session_id")
        if cid is not None and cid in self.clients:
            target = cid
        elif (
            sid is not None and sid == self.station_session and self.station_client in self.clients
        ):
            target = self.station_client  # its page reconnected and said hello again
        elif cid is None:
            target = None  # started without a page (tests, the bus): every phone and console
        else:
            target = -1  # its page is gone and nobody has taken the save over yet
        if topic == C.ENROLL_STATE:
            if body.get("phase") in ("done", "cancelled", "fallback"):
                if sid == self.station_session:
                    self.station_session = self.station_state = self.station_client = None
                self._log_event(f"Station save: {body.get('phase')}")
            else:
                self.station_session, self.station_state = sid, body
                if target is not None and target != -1:
                    self.station_client = target
        if target != -1:
            self._station_send(target, topic, body, preview)

    def _station_send(self, target: int | None, topic: str, body: dict, preview: bool) -> None:
        if target is None:
            clients = [c for c in self.clients.values() if c.role in ("phone", "console")]
        elif target in self.clients:
            clients = [self.clients[target]]
        else:
            return
        for client in clients:
            if preview:
                client.push_preview(body)
            else:
                self._push(client, C.WS_STATION[topic], body)

    def _on_alert(self, ev: Any) -> None:
        body = to_jsonable(ev)
        self.broadcast(C.WS_ALERT, body)
        if body.get("state") != "update":
            self._log_event(f"Alert {body.get('kind')} ({body.get('side')}): {body.get('state')}")

    def _on_reply_spoken(self, ev: Any) -> None:
        body = to_jsonable(ev)
        self.broadcast(C.WS_REPLY_SPOKEN, body)
        self._log_event(f"Spoke a reply ({body.get('voice')})")

    def _on_camera(self, ev: Any) -> None:
        on = bool(get(ev, "on", True))
        self.broadcast(C.WS_CAMERA, {"on": on})
        self._log_event("Camera on" if on else "Camera off")

    def _on_paused(self, ev: Any) -> None:
        paused = bool(get(ev, "paused", False))
        self.broadcast(C.WS_PAUSED, {"paused": paused})
        self._log_event("Paused" if paused else "Resumed")

    def _on_enroll_result(self, ev: Any) -> None:
        body = to_jsonable(ev)
        self.broadcast(C.WS_ENROLL_RESULT, body)
        result = "ok" if body.get("ok") else (body.get("reason") or "failed")
        self._log_event(f"Enroll {body.get('part')}: {result}")

    def _on_person_changed(self, ev: Any) -> None:
        body = to_jsonable(ev)
        self.broadcast(C.WS_PERSON_CHANGED, body)
        self._log_event(f"{body.get('name') or body.get('person_id')} {body.get('action')}")
        self.refresh_people()

    def refresh_people(self) -> None:
        """Re-read data/people off the loop, then send `people` to the console and phone."""
        loop = self.loop
        if loop is None:
            return
        future = loop.run_in_executor(None, load_people, self.people_dir)

        def done(fut: asyncio.Future) -> None:
            if fut.cancelled() or fut.exception() is not None:
                return
            self.people = fut.result()
            self.broadcast(C.WS_PEOPLE, {"people": self.people})

        future.add_done_callback(done)

    def _on_hw_link(self, ev: Any) -> None:
        body = to_jsonable(ev)
        self.broadcast(C.WS_HW_LINK, body)
        connected = bool(body.get("connected"))
        if connected != self._hw_connected:
            self._hw_connected = connected
            driver = body.get("driver")
            self._log_event(
                f"Arduino connected ({driver})" if connected else "Arduino disconnected"
            )

    def _on_status(self, ev: Any) -> None:
        body = to_jsonable(ev)
        self.latest_status = body
        self.broadcast(C.WS_STATUS, body)

    def _on_status_part(self, ev: Any) -> None:
        part, ok = get(ev, "part"), bool(get(ev, "ok", True))
        if part is None:
            return
        before = self._part_ok.get(part)
        self._part_ok[part] = ok
        if before is not None and before != ok:
            detail = get(ev, "detail", "") or ""
            self._log_event(
                f"{part}: {'ok' if ok else 'problem'}{' - ' + detail if detail else ''}"
            )
        elif before is None and not ok:
            self._log_event(f"{part}: {get(ev, 'detail', '') or 'not running'}")

    def _on_forget(self, ev: Any) -> None:
        self.save_pending = None
        self.captions.clear()
        self.translations.clear()
        self.event_log.clear()
        self._log_event("Session forgotten")

    def _on_command_event(self, ev: Any) -> None:
        args = get(ev, "args") or {}
        pending = self.save_pending
        if (
            get(ev, "name") in ("enroll.start", "enroll.station")
            and pending
            and isinstance(args, dict)
            and args.get("action", "start") == "start"
            and args.get("request_id") == pending.get("request_id")
        ):
            self.save_pending = None  # consent given; the enrollment reports from here
        if get(ev, "name") == "mark":
            note = (get(ev, "args") or {}).get("note", "")
            self._log_event(f"Mark: {note}" if note else "Mark")

    # ------------------------------------------------------------------ pages
    def add_client(self, ws: Any) -> Client:
        client = Client(ws, next(self._ids))
        self.clients[client.id] = client
        return client

    def remove_client(self, client: Client) -> None:
        client.closed = True
        self.clients.pop(client.id, None)
        self._recount()

    def _recount(self) -> None:
        clients = list(self.clients.values())
        self._frame_clients = sum(1 for c in clients if c.frames)
        self._console_clients = sum(1 for c in clients if c.role == "console")

    def on_text(self, client: Client, text: str) -> None:
        """A JSON message from a page (runs on the loop)."""
        try:
            msg = json.loads(text)
        except ValueError:
            log.info("Page %s sent invalid JSON", client.id)
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == C.WS_HELLO:
            self.hello(client, msg)
        elif kind == C.WS_COMMAND:
            name, args = msg.get("name"), msg.get("args") or {}
            if name == "enroll.station" and isinstance(args, dict):
                # V-23: the station's preview and meter go back to this page only
                args = {**args, "client_id": client.id}
            if self.router is not None:
                try:
                    self._commands.submit(self._run_command, name, args)
                except RuntimeError:  # shutting down
                    pass
        else:
            log.info("Page %s sent unknown message type %r", client.id, kind)

    def _run_command(self, name: Any, args: Any) -> None:
        try:
            self.router.handle(name, args)
        except Exception:
            log.exception("Command %r failed", name)

    def hello(self, client: Client, msg: dict[str, Any]) -> None:
        role = msg.get("role")
        if role not in C.ROLES:
            log.info(
                "Page %s said hello with unknown role %r; treating it as lens", client.id, role
            )
            role = "lens"
        client.role = role
        client.frames = bool(msg.get("frames", False))
        self._recount()
        log.info(
            "Page %s connected as %s%s", client.id, role, " with frames" if client.frames else ""
        )
        client.push(
            C.WS_WELCOME,
            {
                "session_id": self.session_id,
                "paused": self.paused,
                "camera_on": self.camera_on,
                "config": self.welcome_config,
            },
        )
        if role in C.WS_AUDIENCE[C.WS_PEOPLE]:
            client.push(C.WS_PEOPLE, {"people": self.people})
        if role in C.WS_AUDIENCE[C.WS_HW_LINK] and self._hw_link is not None:
            client.push(C.WS_HW_LINK, to_jsonable(self._hw_link))
        pending = self.save_pending
        if pending is not None and self.clock() < float(pending.get("expires_t") or 0):
            client.push(C.WS_SAVE_REQUEST, pending)  # a page that opens late can still consent
        state = self.station_state
        if role == "phone" and state is not None and self.station_client not in self.clients:
            # the phone following a station save reconnected (or another took over): resume it
            self.station_client = client.id
            client.push(C.WS_ENROLL_STATE, state)
        if role == "console":
            if self.latest_status is not None:
                client.push(C.WS_STATUS, self.latest_status)
            for entry in list(self.event_log):
                client.push(C.WS_EVENT_LOG, entry)

    async def sender(self, client: Client) -> None:
        """Send one page its queued JSON messages, then its newest frame."""
        ws = client.ws
        try:
            while not client.closed:
                await client.wake.wait()
                client.wake.clear()
                while client.queue and not client.closed:
                    await ws.send_text(client.queue.popleft())
                preview = client.preview
                if preview is not None and not client.closed:
                    client.preview = None
                    await ws.send_text(preview)
                frame = client.frame
                if frame is not None and not client.closed:
                    client.frame = None
                    await ws.send_bytes(frame)
                    client.frames_sent += 1
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 - the page went away mid-send
            log.debug("Page %s send failed: %s", client.id, exc)
            client.closed = True
        try:
            await ws.close()
        except Exception as exc:  # noqa: BLE001 - already gone
            log.debug("Page %s close: %s", client.id, exc)

    async def endpoint(self, websocket: WebSocket) -> None:
        """The `/ws` route."""
        await websocket.accept()
        client = self.add_client(websocket)
        task = asyncio.create_task(self.sender(client))
        try:
            while not client.closed:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                text = message.get("text")
                if text is not None:
                    self.on_text(client, text)
        except Exception as exc:  # noqa: BLE001 - a page going away is normal
            log.debug("Page %s: %s", client.id, exc)
        finally:
            self.remove_client(client)
            client.wake.set()
            task.cancel()
            log.info("Page %s (%s) disconnected", client.id, client.role)

    # ------------------------------------------------------------------ frames
    def _send_frame(self, data: bytes) -> None:
        for client in list(self.clients.values()):
            if client.frames:
                client.push_frame(data)

    def encode_frame(self, ev: Any) -> bytes | None:
        """Binary frame message: header + 1280x720 JPEG."""
        import cv2

        image = get(ev, "image")
        if image is None:
            return None
        if image.shape[1] != self.frame_w or image.shape[0] != self.frame_h:
            image = cv2.resize(image, (self.frame_w, self.frame_h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            return None
        header = struct.pack(
            C.FRAME_HEADER_FORMAT, int(get(ev, "frame_no", 0)), float(get(ev, "t", 0.0))
        )
        return header + buf.tobytes()

    def thumbnails(self, frame: Any, scene: Any) -> list[dict[str, Any]]:
        """JPEG crops of the faces in `scene`, cut from `frame`, for enrollment picking."""
        import cv2

        image = get(frame, "image")
        if image is None or scene is None:
            return []
        h, w = image.shape[:2]
        out = []
        for face in get(scene, "faces") or []:
            box = get(face, "box")
            if box is None or len(box) != 4:
                continue
            x, y, bw, bh = (float(v) for v in box)
            m = 0.25 * max(bw, bh)
            x0, y0 = max(0, int(x - m)), max(0, int(y - m))
            x1, y1 = min(w, int(x + bw + m)), min(h, int(y + bh + m))
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            crop = image[y0:y1, x0:x1]
            scale = 112 / crop.shape[0]
            crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), 112))
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                out.append(
                    {
                        "track_id": to_jsonable(get(face, "track_id")),
                        "label": get(face, "label", ""),
                        "jpeg_b64": base64.b64encode(buf.tobytes()).decode("ascii"),
                    }
                )
        return out

    def _encode_loop(self) -> None:
        min_gap = 0.8 / self.max_fps if self.max_fps > 0 else 0.0
        last_no: Any = None
        last_t = last_thumb = last_health = 0.0
        while not self._stop.is_set():
            self._frame_event.wait(0.5)
            self._frame_event.clear()
            if self._stop.is_set():
                return
            now = time.monotonic()
            frame = self._frame
            try:
                if frame is not None and self._frame_clients > 0:
                    frame_no = get(frame, "frame_no")
                    if frame_no != last_no and now - last_t >= min_gap:
                        t0 = time.perf_counter()
                        data = self.encode_frame(frame)
                        ms = 1000 * (time.perf_counter() - t0)
                        prev = self.encode_ms
                        self.encode_ms = ms if prev is None else 0.9 * prev + 0.1 * ms
                        if data is not None:
                            last_no, last_t = frame_no, now
                            self.frames_encoded += 1
                            self._post(self._send_frame, data)
                if (
                    frame is not None
                    and self._console_clients > 0
                    and now - last_thumb >= self.thumb_every_s
                ):
                    last_thumb = now
                    thumbs = self.thumbnails(frame, self._scene_raw)
                    self._post(self.broadcast, C.WS_THUMBNAILS, {"thumbnails": thumbs})
            except Exception:
                log.exception("Frame encoding failed")
            if now - last_health >= 1.0:
                last_health = now
                self._post(self._publish_health)

    def _publish_health(self) -> None:
        """The hub's own `status.part` (runs on the loop, which owns `clients`)."""
        now, encoded = time.monotonic(), self.frames_encoded
        t0, n0 = self._health_mark
        self._health_mark = (now, encoded)
        clients = list(self.clients.values())
        roles: dict[str, int] = {}
        for c in clients:
            roles[c.role or "no hello"] = roles.get(c.role or "no hello", 0) + 1
        enc = self.encode_ms
        self.bus.publish(
            C.STATUS_PART,
            {
                "part": "server",
                "ok": True,
                "detail": f"{len(clients)} page(s)",
                "metrics": {
                    "pages": roles,
                    "frames_out_fps": round((encoded - n0) / max(now - t0, 1e-3), 1),
                    "encode_ms": round(enc, 1) if enc is not None else None,
                    "frames_skipped": sum(c.frames_skipped for c in clients),
                },
            },
        )
