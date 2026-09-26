"""Save a person with a double tap: pick who, ask them for consent, time out.

Section 4 - Pages, Engine & Demo. TODO: P-29. Contracts: docs/contracts.md (2, 3, 4 and
"Save a person").

1. A double tap on the side of the glasses (the touch router's `touch.action` {target: save,
   id: <proposal_id or None>}) or the command `save.start` {track_id?} (key D on the lens)
   asks to save "this person". Who, in this order:
   - the face with an active name proposal ("Sam?"), or one confirmed in the last few
     seconds; a proposal still open is confirmed for the session too (like a tap);
   - otherwise the named person who spoke last (session name, not saved yet);
   - otherwise the most prominent named face in view (largest, most centred).
   Nobody named in view: `save.cancel` {reason: no_name} ("Say their name first"); only saved
   people in view: {reason: already_saved}.
2. It publishes `save.request` {request_id, track_id, name, t, expires_t, ...}; the hub sends
   `save_request` to every page. The phone and the console show a consent sheet that the
   person being saved ticks themselves; the glasses show "Waiting for Sam's OK".
3. Their Save sends the existing `enroll.start` {track_id, name, consent: true, consent_t,
   request_id}; vision (face) then audio (voice) enroll and report `enroll.progress` and
   `enroll.result`. This module never sends `enroll.start` itself: consent only ever comes
   from the person, through a page.
4. Cancel (`save.cancel` {request_id}), no answer within `consent_timeout_s` (60 s), their face
   leaving the view, pause or "forget session" publish `save.cancel` {request_id, reason}.

Bus callbacks only update state under a lock and publish outside it; a small thread checks
the timeout.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from . import contracts as C
from .contracts import get

log = logging.getLogger(__name__)

DEFAULTS = {
    "consent_timeout_s": 60.0,  # a save request waits this long for the person's consent
    "proposal_grace_s": 5.0,  # a proposal confirmed this recently still counts as "this person"
    "speaker_recent_s": 10.0,  # the last named speaker counts for this long
    "scene_stale_s": 2.0,  # faces from an older scene are not "in view"
    "enrolling_s": 90.0,  # a started enrollment blocks new requests for that face this long
}
NAMED = "named"
ENROLLED = "enrolled"


class SaveFlow:
    """Turns "save this person" into a consent request and follows it until it is answered."""

    def __init__(
        self, bus, config: dict[str, Any] | None = None, clock: Callable[[], float] | None = None
    ) -> None:
        config = config or {}
        cfg = {**DEFAULTS, **(config.get("save") or {})}
        vision = config.get("vision") or {}
        self.bus = bus
        self.clock = clock or config.get("clock") or time.perf_counter
        self.timeout_s = float(cfg["consent_timeout_s"])
        self.grace_s = float(cfg["proposal_grace_s"])
        self.speaker_s = float(cfg["speaker_recent_s"])
        self.stale_s = float(cfg["scene_stale_s"])
        self.enrolling_s = float(cfg["enrolling_s"])
        self.cam_w = float(vision.get("width", 1920))
        self.cam_h = float(vision.get("height", 1080))
        self._lock = threading.Lock()
        self.faces: dict[int, dict] = {}  # track_id -> {label, status, box, person_id}
        self.scene_t: float | None = None
        self.proposals: dict[str, dict] = {}  # proposal_id -> {track_id, name, state, t, expires_t}
        self.speaker: dict | None = None  # {track_id, t}
        self.active: dict | None = None  # the request waiting for consent
        self.enrolling: dict[int, float] = {}  # track_id -> clock when its enrollment started
        self._unsubs: list[Callable[[], None]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> None:
        if self._unsubs:
            return
        subs = {
            C.TOUCH_ACTION: self._on_touch,
            C.COMMAND: self._on_command,
            C.SCENE: self._on_scene,
            C.CAPTION: self._on_caption,
            C.NAME_PROPOSAL: self._on_proposal,
            C.VISION_TRACK_LOST: self._on_track_lost,
            C.ENROLL_RESULT: self._on_enroll_result,
            C.PAUSED: self._on_paused,
            C.SESSION_FORGET: self._on_forget,
        }
        self._unsubs = [self.bus.subscribe(topic, cb) for topic, cb in subs.items()]

    def start(self) -> None:
        self.connect()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="save-flow", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            self.check_timeout()

    def _publish(self, events: list[tuple[str, dict]]) -> None:
        for topic, event in events:
            self.bus.publish(topic, event)

    # ------------------------------------------------------------------ bus: world state
    def _on_scene(self, ev: Any) -> None:
        faces = {}
        for f in get(ev, "faces") or []:
            tid = get(f, "track_id")
            if tid is None:
                continue
            faces[int(tid)] = {
                "label": get(f, "label", "") or "",
                "status": get(f, "status", "unknown") or "unknown",
                "box": list(get(f, "box") or [0, 0, 0, 0]),
            }
        with self._lock:
            self.faces = faces
            self.scene_t = self.clock()

    def _on_caption(self, ev: Any) -> None:
        speaker = get(ev, "speaker")
        if get(speaker, "kind") not in ("face", "probable_face"):
            return
        tid = get(speaker, "track_id")
        if tid is None:
            return
        with self._lock:
            self.speaker = {"track_id": int(tid), "t": self.clock()}

    def _on_proposal(self, ev: Any) -> None:
        pid = get(ev, "proposal_id")
        if pid is None:
            return
        state = get(ev, "state")
        with self._lock:
            if state in ("proposed", "confirmed"):
                self.proposals[str(pid)] = {
                    "track_id": get(ev, "track_id"),
                    "name": get(ev, "name", "") or "",
                    "state": state,
                    "t": self.clock(),
                    "expires_t": get(ev, "expires_t"),
                }
            else:  # rejected, expired
                self.proposals.pop(str(pid), None)

    def _on_track_lost(self, ev: Any) -> None:
        tid = get(ev, "track_id")
        events = []
        with self._lock:
            self.faces.pop(tid, None)
            for key in [k for k, p in self.proposals.items() if p["track_id"] == tid]:
                self.proposals.pop(key)
            if self.active and self.active["track_id"] == tid:
                events = self._cancel_locked("lost")
        self._publish(events)

    def _on_enroll_result(self, ev: Any) -> None:
        tid = get(ev, "track_id")
        if tid is None:
            return
        if get(ev, "part") == "voice" or not get(ev, "ok"):
            with self._lock:
                self.enrolling.pop(int(tid), None)

    def _on_paused(self, ev: Any) -> None:
        if not get(ev, "paused"):
            return
        with self._lock:
            events = self._cancel_locked("cancelled") if self.active else []
        self._publish(events)

    def _on_forget(self, ev: Any = None) -> None:
        with self._lock:
            events = self._cancel_locked("cancelled") if self.active else []
            self.proposals.clear()
            self.speaker = None
            self.enrolling.clear()
        self._publish(events)

    # ------------------------------------------------------------------ bus: triggers
    def _on_touch(self, ev: Any) -> None:
        if get(ev, "target") == "save":
            self.request(proposal_id=get(ev, "id"))

    def _on_command(self, ev: Any) -> None:
        name = get(ev, "name")
        args = get(ev, "args") or {}
        if not isinstance(args, dict):
            args = {}
        if name == "save.start":
            tid = args.get("track_id")
            self.request(track_id=int(tid) if isinstance(tid, (int, float)) else None)
        elif name == "save.cancel":
            with self._lock:
                rid = args.get("request_id")
                match = self.active and (rid is None or rid == self.active["request_id"])
                events = self._cancel_locked("declined") if match else []
            self._publish(events)
        elif name == "enroll.start" and args.get("consent") is True:
            self._on_consent(args)

    def _on_consent(self, args: dict) -> None:
        """The person ticked consent on a page: the enrollment runs, the request is answered."""
        tid = args.get("track_id")
        with self._lock:
            if isinstance(tid, (int, float)):
                self.enrolling[int(tid)] = self.clock()
            a = self.active
            if a and (args.get("request_id") == a["request_id"] or tid == a["track_id"]):
                log.info("Save request %s: consent given", a["request_id"])
                self.active = None

    # ------------------------------------------------------------------ choosing who
    def _visible(self, now: float) -> dict[int, dict]:
        if self.scene_t is None or now - self.scene_t > self.stale_s:
            return {}
        return self.faces

    def _prominence(self, face: dict) -> float:
        x, y, w, h = (float(v) for v in face["box"])
        cx, cy = x + w / 2, y + h / 2
        off = abs(cx - self.cam_w / 2) / (self.cam_w / 2) + abs(cy - self.cam_h / 2) / (
            self.cam_h / 2
        )
        return w * h * (1.0 - 0.35 * min(1.0, off / 2))

    def choose(
        self, track_id: int | None = None, proposal_id: str | None = None
    ) -> tuple[dict | None, str, dict | None]:
        """Who "save this person" means now: (target, "", None) or (None, reason, face).

        target = {track_id, name, proposal_id, confirm}; `confirm` is True when the name is
        still a proposal the save also confirms. Call with the lock held.
        """
        now = self.clock()
        visible = self._visible(now)

        def from_proposal(pid: str, p: dict) -> dict | None:
            if p["track_id"] not in visible or not p["name"]:
                return None
            if p["state"] == "proposed":
                expires = p.get("expires_t")
                # expires_t is on the engine clock; a missing one never expires here
                if isinstance(expires, (int, float)) and expires <= now and pid != proposal_id:
                    return None
            elif now - p["t"] > self.grace_s:
                return None
            status = visible[p["track_id"]]["status"]
            if status == ENROLLED:
                return None
            return {
                "track_id": int(p["track_id"]),
                "name": p["name"],
                "proposal_id": pid,
                "confirm": p["state"] == "proposed",
            }

        # a face asked for by the page
        if track_id is not None:
            face = visible.get(track_id)
            if face is None:
                return None, "no_name", None  # not in view
            for pid, p in self.proposals.items():
                if p["track_id"] == track_id and (t := from_proposal(pid, p)):
                    return t, "", None
            if face["status"] == NAMED and face["label"]:
                return (
                    {
                        "track_id": track_id,
                        "name": face["label"],
                        "proposal_id": None,
                        "confirm": False,
                    },
                    "",
                    None,
                )
            reason = "already_saved" if face["status"] == ENROLLED else "no_name"
            return None, reason, face

        # 1. the proposal the double tap answered, else the newest open or just-confirmed one
        asked = self.proposals.get(str(proposal_id)) if proposal_id is not None else None
        if asked is not None and (t := from_proposal(str(proposal_id), asked)):
            return t, "", None
        for pid, p in sorted(self.proposals.items(), key=lambda kv: -kv[1]["t"]):
            if t := from_proposal(pid, p):
                return t, "", None
        # 2. the named person who spoke last
        sp = self.speaker
        if sp and now - sp["t"] <= self.speaker_s:
            face = visible.get(sp["track_id"])
            if face and face["status"] == NAMED and face["label"]:
                return (
                    {
                        "track_id": sp["track_id"],
                        "name": face["label"],
                        "proposal_id": None,
                        "confirm": False,
                    },
                    "",
                    None,
                )
        # 3. the most prominent named face in view
        named = [(tid, f) for tid, f in visible.items() if f["status"] == NAMED and f["label"]]
        if named:
            tid, face = max(named, key=lambda tf: self._prominence(tf[1]))
            return (
                {"track_id": tid, "name": face["label"], "proposal_id": None, "confirm": False},
                "",
                None,
            )
        saved = [f for f in visible.values() if f["status"] == ENROLLED]
        if saved:
            return None, "already_saved", max(saved, key=self._prominence)
        return None, "no_name", None

    # ------------------------------------------------------------------ requests
    def request(self, track_id: int | None = None, proposal_id: str | None = None) -> dict | None:
        """Start a save request (or say why not). Returns the request, if one was made."""
        events: list[tuple[str, dict]] = []
        req = None
        with self._lock:
            now = self.clock()
            for tid, t0 in list(self.enrolling.items()):
                if now - t0 > self.enrolling_s:
                    self.enrolling.pop(tid)
            target, reason, face = self.choose(track_id, proposal_id)
            if target is None:
                name = face["label"] if face else ""
                log.info("Save: nobody to save (%s)", reason)
                events.append((C.SAVE_CANCEL, self._cancel_event(None, reason, None, name)))
                events.append((C.HW_PATTERN, {"name": "NO", "side": "B"}))
            elif target["track_id"] in self.enrolling:
                log.info("Save: %s is already being saved", target["name"])
            elif self.active and self.active["track_id"] == target["track_id"]:
                req = self.active  # asked again: show the same request again
                events.append((C.SAVE_REQUEST, dict(req)))
            else:
                if self.active:
                    events += self._cancel_locked("replaced")
                req = {
                    "request_id": f"save-{uuid.uuid4().hex[:10]}",
                    "track_id": target["track_id"],
                    "name": target["name"],
                    "t": round(now, 3),
                    "expires_t": round(now + self.timeout_s, 3),
                    "person_id": None,
                    "proposal_id": target["proposal_id"],
                }
                self.active = req
                if target["confirm"]:
                    # saving someone also confirms their proposed name for the session
                    events.append(
                        (
                            C.COMMAND,
                            {
                                "name": "name.answer",
                                "args": {"proposal_id": target["proposal_id"], "accept": True},
                            },
                        )
                    )
                events.append((C.SAVE_REQUEST, dict(req)))
                events.append((C.HW_PATTERN, {"name": "OK", "side": "B"}))
                log.info("Save request %s for track %s", req["request_id"], req["track_id"])
        self._publish(events)
        return req

    @staticmethod
    def _cancel_event(request_id, reason: str, track_id, name: str) -> dict:
        return {"request_id": request_id, "reason": reason, "track_id": track_id, "name": name}

    def _cancel_locked(self, reason: str) -> list[tuple[str, dict]]:
        a, self.active = self.active, None
        if a is None:
            return []
        log.info("Save request %s ended: %s", a["request_id"], reason)
        return [
            (C.SAVE_CANCEL, self._cancel_event(a["request_id"], reason, a["track_id"], a["name"]))
        ]

    def check_timeout(self) -> None:
        with self._lock:
            a = self.active
            expired = a is not None and self.clock() >= a["expires_t"]
            events = self._cancel_locked("timeout") if expired else []
        self._publish(events)
