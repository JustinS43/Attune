"""WebSocket hub, static routes and page commands through FastAPI's TestClient."""

from __future__ import annotations

import base64
import contextlib
import struct

import cv2
import numpy as np
import pytest
from attune.core import contracts as C
from attune.server.ws import to_jsonable

from .conftest import recv, recv_type, wait_for


@contextlib.contextmanager
def page(client, role, frames=False):
    """A connected page that has said hello; closed even when the test fails."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "role": role, "frames": frames})
        yield ws, recv_type(ws, "welcome")


def frame_event(frame_no=7, t=12.5, w=1920, h=1080):
    image = np.zeros((h, w, 3), np.uint8)
    image[: h // 2] = (0, 0, 255)  # top half red (BGR)
    return C.Frame(frame_no, t, image)


def scene_event(box=(960, 540, 192, 108)):
    face = C.FaceState(4, list(box), "Sam", "enrolled", np.float32(0.04), True, False)
    return C.Scene(
        10, 12.5, [face], [C.Offscreen(None, "Person in blue", "left")], False
    )


def caption_event(utt="u1", text="Hi, my name is Sam", final=True):
    speaker = C.Speaker("face", 4, "sam-abc123", "Sam", "none")
    return C.Caption(
        utt, speaker, text, final, "en", [("Hi", 1.0, 1.2), ("Sam", 1.5, 1.8)]
    )


# ---------------------------------------------------------------- static routes


def test_static_routes(hub_env):
    c = hub_env.client
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/lens/"
    assert c.get("/lens/").status_code == 200
    assert c.get("/shared/ws.js").status_code == 200
    assert c.get("/phone/").status_code == 200
    assert c.get("/demo/").status_code == 200
    assert c.get("/data/reels/film/timeline.json").json() == {"ok": True}
    assert c.get("/data/people/sam-abc123/meta.json").status_code == 404
    assert c.get("/data/people/").status_code == 404
    assert (
        c.get("/data/reels/film/../../people/sam-abc123/meta.json").status_code == 404
    )


# ---------------------------------------------------------------- hello / welcome / seq


def test_hello_welcome_and_people(hub_env):
    with page(hub_env.client, "console") as (ws, welcome):
        assert welcome["seq"] == 1
        assert welcome["session_id"] == "test-session"
        assert welcome["paused"] is False
        assert welcome["config"] == {
            "bubble_chars": 42,
            "bubble_lines": 2,
            "bubble_fade_s": 4,
            "presets": ["Nice to meet you", "One moment"],
        }
        people = recv_type(ws, "people")
        assert people["people"] == [
            {
                "person_id": "sam-abc123",
                "name": "Sam",
                "consent_t": "2026-09-26T10:00:00-04:00",
                "has_face": True,
                "has_voice": False,
            }
        ]


def test_seq_increases_per_page(hub_env):
    with page(hub_env.client, "lens") as (ws, welcome):
        seqs = [welcome["seq"]]
        for i in range(5):
            hub_env.bus.publish(C.CAPTION, caption_event(f"u{i}"))
        for _ in range(5):
            seqs.append(recv_type(ws, "caption")["seq"])
        assert seqs == [1, 2, 3, 4, 5, 6]


def test_nothing_before_hello(hub_env):
    with hub_env.client.websocket_connect("/ws") as ws:
        hub_env.bus.publish(C.CAPTION, caption_event())
        ws.send_json({"type": "hello", "role": "phone", "frames": False})
        first = recv(ws)
        assert first["type"] == "welcome" and first["seq"] == 1


# ---------------------------------------------------------------- frames


def test_frames_only_to_pages_that_asked_and_layout(hub_env):
    with (
        page(hub_env.client, "lens", frames=True) as (lens, _),
        page(hub_env.client, "console", frames=False) as (console, _),
    ):
        hub_env.bus.publish(C.VISION_FRAME, frame_event())
        data = recv(lens)
        while isinstance(data, dict):  # skip any JSON that got there first
            data = recv(lens)
        frame_no, t = struct.unpack("<Qd", data[:16])
        assert (frame_no, t) == (7, 12.5)
        assert data[16:18] == b"\xff\xd8"  # JPEG
        image = cv2.imdecode(np.frombuffer(data[16:], np.uint8), cv2.IMREAD_COLOR)
        assert image.shape == (720, 1280, 3)
        assert image[100, 100, 2] > 200 and image[600, 100, 2] < 50  # red on top only
        # The console did not ask for frames: everything it gets is JSON.
        hub_env.bus.publish(C.CAPTION, caption_event())
        seen: list = []
        recv_type(console, "caption", seen=seen)
        assert all(isinstance(m, dict) for m in seen)


def test_slow_page_skips_frames_but_keeps_messages(hub_env):
    """Frames replace each other in the one-slot buffer; JSON messages all arrive."""
    with page(hub_env.client, "lens", frames=True) as (lens, _):
        hub = hub_env.hub
        client = next(c for c in hub.clients.values() if c.role == "lens")
        client.push_frame(b"old")
        client.push_frame(b"new")
        assert client.frames_skipped == 1 and client.frame == b"new"
        for i in range(50):
            hub_env.bus.publish(C.CAPTION, caption_event(f"u{i}"))
        got = [recv_type(lens, "caption")["utt_id"] for _ in range(50)]
        assert got == [f"u{i}" for i in range(50)]


# ---------------------------------------------------------------- scene


def test_scene_boxes_scaled_to_1280x720(hub_env):
    with page(hub_env.client, "lens") as (ws, _):
        hub_env.bus.publish(C.SCENE, scene_event())
        scene = recv_type(ws, "scene")
        face = scene["faces"][0]
        assert face["box"] == [640.0, 360.0, 128.0, 72.0]
        assert face["label"] == "Sam" and face["is_speaker"] is True
        assert face["lip_score"] == pytest.approx(0.04)
        assert scene["offscreen"] == [
            {"person_id": None, "label": "Person in blue", "side": "left"}
        ]
        assert scene["you_speaking"] is False


def test_scene_scale_follows_camera_frame_size(hub_env):
    with page(hub_env.client, "console") as (ws, _):
        hub_env.bus.publish(C.VISION_FRAME, frame_event(w=640, h=480))
        hub_env.bus.publish(C.SCENE, scene_event(box=(320, 240, 64, 48)))
        face = recv_type(ws, "scene")["faces"][0]
        assert face["box"] == [640.0, 360.0, 128.0, 72.0]


def test_phone_gets_no_scene(hub_env):
    with page(hub_env.client, "phone") as (phone, _):
        hub_env.bus.publish(C.SCENE, scene_event())
        hub_env.bus.publish(C.CAPTION, caption_event())
        seen: list = []
        recv_type(phone, "caption", seen=seen)
        assert not any(isinstance(m, dict) and m.get("type") == "scene" for m in seen)


# ---------------------------------------------------------------- captions and relays


def test_caption_and_translation_merge(hub_env):
    with page(hub_env.client, "lens") as (ws, _):
        hub_env.bus.publish(C.CAPTION, caption_event("u9", "Hola, me llamo Sam"))
        first = recv_type(ws, "caption")
        assert first["text"] == "Hola, me llamo Sam" and "translation" not in first
        assert first["speaker"]["label"] == "Sam"
        assert first["words"] == [["Hi", 1.0, 1.2], ["Sam", 1.5, 1.8]]
        hub_env.bus.publish(
            C.CAPTION_TRANSLATION,
            C.CaptionTranslation("u9", "es", "Hi, my name is Sam"),
        )
        again = recv_type(ws, "caption")
        assert again["utt_id"] == "u9" and again["text"] == "Hola, me llamo Sam"
        assert again["translation"] == "Hi, my name is Sam"
        assert again["seq"] > first["seq"]
        # an unknown utterance's translation is held until its caption arrives
        hub_env.bus.publish(
            C.CAPTION_TRANSLATION,
            {"utt_id": "u10", "source_lang": "es", "text_en": "Later"},
        )
        hub_env.bus.publish(C.CAPTION, caption_event("u10", "Luego"))
        later = recv_type(ws, "caption")
        assert later["utt_id"] == "u10" and later["translation"] == "Later"


def test_relays_and_audiences(hub_env):
    with (
        page(hub_env.client, "lens") as (lens, _),
        page(hub_env.client, "console") as (console, _),
    ):
        bus = hub_env.bus
        bus.publish(
            C.NAME_PROPOSAL,
            {
                "proposal_id": "p1",
                "track_id": 4,
                "name": "Sam",
                "state": "proposed",
                "expires_t": 20.0,
            },
        )
        bus.publish(
            C.ALERT,
            {
                "alert_id": "a1",
                "kind": "smoke",
                "side": "left",
                "confidence": 0.9,
                "state": "start",
            },
        )
        bus.publish(C.REPLY_SUGGESTIONS, {"options": ["Yes", "No", "Maybe"]})
        bus.publish(C.REPLY_SPOKEN, {"text": "Hello", "voice": "kokoro", "t": 3.0})
        bus.publish(C.ENROLL_RESULT, C.EnrollResult("sam-abc123", "face", True, "", 4))
        bus.publish(
            C.HW_LINK, {"connected": True, "firmware": "1.0", "driver": "TB6612"}
        )
        bus.publish(C.STATUS, {"fps": 30.0, "parts": {}})
        assert recv_type(lens, "name_proposal")["name"] == "Sam"
        assert recv_type(lens, "alert")["kind"] == "smoke"
        assert recv_type(lens, "reply_suggestions")["options"] == [
            "Yes",
            "No",
            "Maybe",
        ]
        assert recv_type(lens, "reply_spoken")["voice"] == "kokoro"
        # console-only and console/phone messages
        skipped: list = []
        enroll = recv_type(console, "enroll_result", seen=skipped)
        assert enroll == {
            **enroll,
            "person_id": "sam-abc123",
            "part": "face",
            "ok": True,
            "track_id": 4,
        }
        assert recv_type(console, "hw_link", seen=skipped)["driver"] == "TB6612"
        assert recv_type(console, "status", seen=skipped)["fps"] == 30.0
        logs = [m["text"] for m in skipped if m.get("type") == "event_log"]
        assert "Alert smoke (left): start" in logs and "Name Sam: proposed" in logs
        assert "Enroll face: ok" in logs and "Arduino connected (TB6612)" in logs
        # the lens never gets console-only messages: prove it by sending a marker caption
        bus.publish(C.CAPTION, caption_event("marker"))
        seen: list = []
        recv_type(lens, "caption", seen=seen)
        kinds = {m.get("type") for m in seen if isinstance(m, dict)}
        assert not kinds & {
            "status",
            "event_log",
            "enroll_result",
            "hw_link",
            "people",
            "thumbnails",
        }


def test_late_pages_get_the_current_hw_link(hub_env):
    hub_env.bus.publish(
        C.HW_LINK, {"connected": True, "firmware": "1.0", "driver": "L298"}
    )
    with page(hub_env.client, "phone") as (phone, _):
        assert recv_type(phone, "hw_link")["driver"] == "L298"


def test_person_changed_resends_people(hub_env):
    with page(hub_env.client, "console") as (console, _):
        recv_type(console, "people")
        person = hub_env.data / "people" / "ana-1"
        person.mkdir()
        (person / "meta.json").write_text('{"name": "Ana", "consent_t": null}')
        (person / "voice.json").write_text("{}")
        hub_env.bus.publish(
            C.PERSON_CHANGED, C.PersonChanged("ana-1", "Ana", "enrolled")
        )
        assert recv_type(console, "person_changed")["action"] == "enrolled"
        people = recv_type(console, "people")["people"]
        ana = next(p for p in people if p["person_id"] == "ana-1")
        assert ana["has_voice"] is True and ana["has_face"] is False


def test_thumbnails_for_console(hub_env):
    with page(hub_env.client, "console") as (console, _):
        hub_env.bus.publish(C.SCENE, scene_event(box=(100, 100, 200, 200)))
        hub_env.bus.publish(C.VISION_FRAME, frame_event())
        msg = recv_type(console, "thumbnails")
        while not msg["thumbnails"]:
            hub_env.bus.publish(C.VISION_FRAME, frame_event())
            msg = recv_type(console, "thumbnails")
        thumb = msg["thumbnails"][0]
        assert thumb["track_id"] == 4
        raw = base64.b64decode(thumb["jpeg_b64"])
        assert (
            cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR).shape[0] == 112
        )


# ---------------------------------------------------------------- commands


def test_pause_toggle_publishes_and_broadcasts(hub_env):
    with (
        page(hub_env.client, "lens") as (lens, _),
        page(hub_env.client, "phone") as (phone, _),
    ):
        lens.send_json({"type": "command", "name": "pause.toggle", "args": {}})
        assert recv_type(lens, "paused")["paused"] is True
        assert recv_type(phone, "paused")["paused"] is True
        assert wait_for(lambda: hub_env.rec[C.PAUSED] == [{"paused": True}])
        assert wait_for(
            lambda: {"name": "pause.toggle", "args": {}} in hub_env.rec[C.COMMAND]
        )
        # a new page is told it is paused
        with page(hub_env.client, "console") as (_other, welcome):
            assert welcome["paused"] is True
            lens.send_json({"type": "command", "name": "pause.toggle"})
            assert recv_type(lens, "paused")["paused"] is False
            assert wait_for(lambda: hub_env.rec[C.PAUSED][-1] == {"paused": False})


def test_bus_pause_command_toggles_once(hub_env):
    """Section 3's touch router publishes touch.action AND command pause.toggle: one flip."""
    with page(hub_env.client, "lens") as (lens, _):
        hub_env.bus.publish(
            C.TOUCH_ACTION, {"target": "pause", "id": None, "accept": True}
        )
        hub_env.bus.publish(C.COMMAND, {"name": "pause.toggle", "args": {}})
        assert recv_type(lens, "paused")["paused"] is True
        assert hub_env.rec[C.PAUSED] == [{"paused": True}]
        assert hub_env.router.paused is True


def test_commands_routed_to_bus(hub_env):
    with page(hub_env.client, "console") as (ws, _):
        sent = [
            (
                "enroll.start",
                {"track_id": 4, "name": "Sam", "consent": True, "consent_t": "t"},
            ),
            ("speak", {"text": "Hi", "source": "typed"}),
            ("name.answer", {"proposal_id": "p1", "accept": True}),
            ("switch.set", {"key": "translation", "value": False}),
        ]
        for name, args in sent:
            ws.send_json({"type": "command", "name": name, "args": args})
        ws.send_json({"type": "command", "name": "rm.rf", "args": {}})
        ws.send_json({"type": "command", "name": "session.forget", "args": {}})
        assert wait_for(lambda: len(hub_env.rec[C.COMMAND]) == 5)
        got = [(e["name"], e["args"]) for e in hub_env.rec[C.COMMAND]]
        assert got == sent + [("session.forget", {})]
        assert hub_env.rec[C.SESSION_FORGET] == [{}]
        assert hub_env.router.ignored == 1


def test_mark_goes_to_session_log_and_event_log(hub_env):
    with page(hub_env.client, "console") as (ws, _):
        ws.send_json(
            {"type": "command", "name": "mark", "args": {"note": "Sam walks in"}}
        )
        assert recv_type(ws, "event_log")["text"] == "Mark: Sam walks in"
        hub_env.log.start()
        hub_env.log.mark("again")
        hub_env.log.stop()
        text = (hub_env.data / "sessions" / "test-session.jsonl").read_text(
            encoding="utf-8"
        )
        assert "again" in text


def test_session_forget_clears_caption_memory(hub_env):
    with page(hub_env.client, "lens") as (ws, _):
        hub_env.bus.publish(C.CAPTION, caption_event("u1"))
        recv_type(ws, "caption")
        ws.send_json({"type": "command", "name": "session.forget"})
        assert wait_for(lambda: not hub_env.hub.captions)
        # a late translation for a forgotten utterance does not resurrect its text
        hub_env.bus.publish(
            C.CAPTION_TRANSLATION, {"utt_id": "u1", "source_lang": "es", "text_en": "x"}
        )
        hub_env.bus.publish(C.CAPTION, caption_event("u2"))
        seen: list = []
        msg = recv_type(ws, "caption", seen=seen)
        assert msg["utt_id"] == "u2"


def test_bad_messages_are_ignored(hub_env):
    with page(hub_env.client, "lens") as (ws, _):
        ws.send_text("not json")
        ws.send_json([1, 2, 3])
        ws.send_json({"type": "who knows"})
        ws.send_bytes(b"\x00\x01")
        hub_env.bus.publish(C.CAPTION, caption_event())
        assert recv_type(ws, "caption")["utt_id"] == "u1"


# ---------------------------------------------------------------- JSON conversion


def test_to_jsonable_drops_arrays_and_nan():
    ev = C.Appearance(3, "blue", np.zeros((10, 10, 3)))
    assert to_jsonable(ev) == {"track_id": 3, "color": "blue"}
    assert to_jsonable({"a": float("nan"), "b": np.int64(4), "c": (1, 2)}) == {
        "a": None,
        "b": 4,
        "c": [1, 2],
    }
