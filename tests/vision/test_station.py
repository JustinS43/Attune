"""V-23 / A-21: the enrollment station saves a person at the laptop camera and mic.

Fake camera, mic, face finder and face printer (tests/vision/station_fakes.py); the
station's own logic is real: phases, the capture window, hints, the identity check, the
voice step, what is stored and when the devices close.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest
from attune.core import contracts as C
from attune.core.bus import Bus
from attune.station.session import StationEnroller, VisionHooks

from .station_fakes import (
    FakeCamera,
    FakeDetector,
    FakeEmbedder,
    FakeMic,
    energy_vad,
    face_det,
    person,
    speech_like,
)

SAM, ANA = person(1), person(2)

CONFIG = {
    "engine": {"data_dir": "data"},
    "vision": {"camera_name": "Brio 101", "enroll_crops": 8, "enroll_min_crops": 5},
    "audio": {"device_name": "Brio 101"},
    "voice": {"enroll_s": 2.0, "model_path": "models/cam++.onnx"},
    "enroll": {
        "source": "station",
        "face_s": 1.0,
        "face_timeout_s": 3.0,
        "open_timeout_s": 0.5,
        "decision_timeout_s": 5.0,
        "voice_timeout_s": 6.0,
        "preview_fps": 30,
    },
}


class Rec:
    def __init__(self, bus, topics):
        self.events = {t: [] for t in topics}
        for t in topics:
            bus.subscribe(t, self.events[t].append)

    def __getitem__(self, t):
        return self.events[t]

    def phases(self):
        return [e["phase"] for e in self.events[C.ENROLL_STATE]]


class Station:
    """A StationEnroller wired to fakes, a real bus and a temp people folder."""

    def __init__(
        self,
        tmp_path,
        *,
        embed=SAM,
        then=None,
        plan=None,
        audio=None,
        camera=None,
        mic=None,
        enroll=None,
        main_camera=("Brio 101", True),
    ):
        cfg = {**CONFIG, "enroll": {**CONFIG["enroll"], **(enroll or {})}}
        self.bus = Bus()
        self.rec = Rec(
            self.bus,
            [
                C.ENROLL_STATE,
                C.ENROLL_PREVIEW,
                C.ENROLL_LEVEL,
                C.ENROLL_MISMATCH,
                C.ENROLL_PROGRESS,
                C.ENROLL_RESULT,
            ],
        )
        self.order: list[str] = []
        self.saved: list[dict] = []
        self.shared: list = []
        self.embedder = FakeEmbedder(embed, then=then)
        self.cameras: list[FakeCamera] = []
        self.camera_proto = camera
        self.mic = mic or FakeMic(audio if audio is not None else speech_like(6.0))
        self.mic.events = self.order

        def make_camera():
            cam = self.camera_proto() if callable(self.camera_proto) else FakeCamera()
            cam.events = self.order
            self.cameras.append(cam)
            return cam

        def save_face(req):
            self.order.append("save_face")
            self.saved.append(req)
            return {"person_id": f"sam-{len(self.saved)}"}

        hooks = VisionHooks(lambda: main_camera, self.shared.append, save_face)
        self.enroller = StationEnroller(
            self.bus,
            cfg,
            FakeDetector(plan),
            self.embedder,
            hooks,
            root=str(tmp_path),
            camera_factory=make_camera,
            mic_factory=lambda: self.mic,
            extractor=lambda audio: unit_vec(len(audio)),
            vad_factory=lambda: energy_vad,
        )
        self.people = tmp_path / "data" / "people"

    def start(self, **args):
        base = {
            "action": "start",
            "name": "Sam",
            "consent": True,
            "consent_t": 1700000000.0,
        }
        self.enroller.command({**base, **args}, None)

    def send(self, action):
        self.enroller.command({"action": action})

    def wait_phase(self, phase, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if phase in self.rec.phases():
                return True
            time.sleep(0.01)
        raise AssertionError(f"never reached {phase}: {self.rec.phases()}")

    def wait_closed(self, timeout=10.0):
        s = self.enroller.session
        assert s is not None and s.closed.wait(timeout), self.rec.phases()


def unit_vec(n):
    v = np.zeros(192, np.float32)
    v[0] = 1.0
    return v


def start_with_glasses(st, glasses, **args):
    base = {
        "action": "start",
        "name": "Sam",
        "consent": True,
        "consent_t": 1700000000.0,
    }
    st.enroller.command({**base, "track_id": 7, "request_id": "save-1", **args}, glasses)


# ------------------------------------------------------------------ the whole save
def test_a_save_without_a_glasses_face_goes_face_then_voice(tmp_path):
    st = Station(tmp_path)
    st.start(client_id=4)
    st.wait_closed()
    phases = st.rec.phases()
    assert phases[0] == "opening" and phases[-1] == "done"
    assert phases.index("face") < phases.index("saving") < phases.index("voice")
    last = st.rec[C.ENROLL_STATE][-1]
    assert last["face_ok"] and last["voice_ok"] and last["person_id"] == "sam-1"
    assert all(e["client_id"] == 4 for e in st.rec[C.ENROLL_STATE])
    # the camera closed before the face was saved, the mic after the speech was collected
    assert st.order.index("camera.stop") < st.order.index("save_face") < st.order.index("mic.start")
    assert st.order[-1] == "mic.stop"
    # no glasses face: nothing to check, and the save isn't linked to a track
    assert not st.rec[C.ENROLL_MISMATCH] and st.saved[0]["track_id"] is None
    assert 5 <= len(st.saved[0]["prints"]) <= 8  # the 8 most varied, at least 5
    results = [(r["part"], r["ok"], r["source"]) for r in st.rec[C.ENROLL_RESULT]]
    assert results == [("voice", True, "station")]  # face result comes from vision's save


def test_only_prints_are_stored(tmp_path):
    st = Station(tmp_path)
    st.start()
    st.wait_closed()
    files = [p.relative_to(st.people).as_posix() for p in st.people.rglob("*") if p.is_file()]
    assert files == ["sam-1/voice.json"]
    record = json.loads((st.people / "sam-1" / "voice.json").read_text())
    assert record["source"] == "station" and record["consent"] is True
    assert set(record) == {"consent", "consent_t", "source", "embedding", "adapted", "automatic"}
    assert record["automatic"] is False


def test_a_station_print_names_the_voice_model_that_made_it(tmp_path):
    class TaggedCAM:  # like CAMExtractor, which knows its model's tag (A-27)
        model_id = "357a834f702b8016"

        def __call__(self, audio):
            return unit_vec(len(audio))

    st = Station(tmp_path)
    st.enroller._extractor = TaggedCAM()
    st.start()
    st.wait_closed()
    record = json.loads((st.people / "sam-1" / "voice.json").read_text())
    assert record["model"] == "357a834f702b8016"


def test_preview_goes_with_a_face_box_and_is_jpeg(tmp_path):
    st = Station(tmp_path)
    st.start(client_id=9)
    st.wait_closed()
    previews = st.rec[C.ENROLL_PREVIEW]
    assert previews and all(p["client_id"] == 9 for p in previews)
    first = previews[0]
    assert first["jpeg"][:2] == b"\xff\xd8" and first["width"] == 360 and first["height"] == 480
    x, _y, w, _h = first["face"]
    assert 0.3 < x + w / 2 < 0.7 and first["ok"] is True


def test_progress_reaches_one_for_both_parts(tmp_path):
    st = Station(tmp_path)
    st.start()
    st.wait_closed()
    prog = st.rec[C.ENROLL_PROGRESS]
    face = [p["fraction"] for p in prog if p["part"] == "face"]
    voice = [p["fraction"] for p in prog if p["part"] == "voice"]
    assert face[-1] == 1.0 and voice[-1] == 1.0
    assert face == sorted(face) and voice == sorted(voice)
    assert all(p["source"] == "station" for p in prog)


def test_level_meter_while_reading(tmp_path):
    st = Station(tmp_path)
    st.start()
    st.wait_closed()
    levels = st.rec[C.ENROLL_LEVEL]
    assert levels and max(x["level"] for x in levels) > 0.5
    assert levels[-1]["need_s"] == 2.0 and levels[-1]["voiced_s"] >= 1.9


# ------------------------------------------------------------------ identity check
def test_the_glasses_face_matches_so_the_save_links_to_its_track(tmp_path):
    st = Station(tmp_path)
    glasses = np.stack([person(1) for _ in range(3)])
    start_with_glasses(st, glasses)
    st.wait_closed()
    assert not st.rec[C.ENROLL_MISMATCH]
    assert st.saved[0]["track_id"] == 7
    assert st.rec[C.ENROLL_RESULT][-1]["track_id"] == 7


def test_not_the_person_you_were_looking_at_then_save_as_someone_new(tmp_path):
    st = Station(tmp_path)
    start_with_glasses(st, np.stack([ANA]))
    st.wait_phase("mismatch")
    miss = st.rec[C.ENROLL_MISMATCH][0]
    assert miss["score"] < miss["threshold"] and miss["track_id"] == 7
    assert not st.saved  # nothing is saved while they decide
    st.send("new_person")
    st.wait_closed()
    assert st.saved[0]["track_id"] is None  # no link to the glasses face
    assert st.rec.phases()[-1] == "done"
    assert all(r["track_id"] is None for r in st.rec[C.ENROLL_RESULT])


def test_mismatch_then_try_again_with_the_right_person(tmp_path):
    st = Station(tmp_path, embed=ANA)  # someone else sat down first
    start_with_glasses(st, np.stack([SAM, SAM]))
    st.wait_phase("mismatch")
    st.embedder.who = SAM  # the right person sits down
    st.send("retry")
    st.wait_closed()
    assert st.rec.phases().count("face") == 2
    assert len(st.cameras) == 2 and all(c.stopped for c in st.cameras)
    assert st.saved[0]["track_id"] == 7


def test_mismatch_cancel_saves_nothing(tmp_path):
    st = Station(tmp_path)
    start_with_glasses(st, np.stack([ANA]))
    st.wait_phase("mismatch")
    st.send("cancel")
    st.wait_closed()
    assert st.rec.phases()[-1] == "cancelled" and not st.saved
    assert not st.people.exists()


# ------------------------------------------------------------------ face step problems
def test_no_face_fails_with_a_reason_then_retry(tmp_path):
    faces = {"on": False}
    st = Station(tmp_path, plan=lambda i: [face_det()] if faces["on"] else [])
    st.start()
    st.wait_phase("face_failed")
    failed = st.rec[C.ENROLL_STATE][-1]
    assert failed["reason"] == "look at the laptop camera"
    assert st.rec[C.ENROLL_RESULT][-1] == {
        "person_id": None,
        "part": "face",
        "ok": False,
        "reason": "look at the laptop camera",
        "track_id": None,
        "source": "station",
        "session_id": failed["session_id"],
    }
    assert st.cameras[0].stopped  # the light is off while they decide
    faces["on"] = True
    st.send("retry")
    st.wait_closed()
    assert st.rec.phases()[-1] == "done"


@pytest.mark.parametrize(
    "plan, hint",
    [
        (lambda i: [face_det(cx=0.15)], "move to the middle"),
        (lambda i: [face_det(width=40)], "come closer"),
        (lambda i: [face_det(width=520)], "move back a little"),
        (lambda i: [face_det(), face_det(cx=0.8, width=230)], "one person at a time"),
    ],
)
def test_lining_up_hints(tmp_path, plan, hint):
    st = Station(tmp_path, plan=plan)
    st.start()
    st.wait_phase("face_failed")
    hints = {p["hint"] for p in st.rec[C.ENROLL_PREVIEW]}
    assert hint in hints
    assert st.rec[C.ENROLL_STATE][-1]["reason"] == hint
    st.send("cancel")
    st.wait_closed()


def test_a_second_person_mid_capture_is_not_mixed_in(tmp_path):
    st = Station(tmp_path, then=(3, ANA))  # someone else leans in after three prints
    st.start()
    st.wait_phase("face_failed")
    assert st.rec[C.ENROLL_STATE][-1]["reason"] == "one person at a time"
    st.send("cancel")
    st.wait_closed()
    assert not st.saved


def test_camera_that_never_starts_falls_back_to_the_glasses(tmp_path):
    st = Station(tmp_path, camera=lambda: FakeCamera(dead=True))
    st.start()
    st.wait_closed()
    last = st.rec[C.ENROLL_STATE][-1]
    assert last["phase"] == "fallback" and last["reason"] == "camera_unavailable"
    assert "didn't start" in last["message"] and st.cameras[0].stopped


def test_cancel_during_the_face_step(tmp_path):
    st = Station(tmp_path, camera=lambda: FakeCamera(delay=0.05))  # still collecting
    st.start()
    st.wait_phase("face")
    st.enroller.cancel("paused")
    st.wait_closed()
    assert st.rec.phases()[-1] == "cancelled" and st.rec[C.ENROLL_STATE][-1]["reason"] == "paused"
    assert st.cameras[0].stopped and not st.saved


# ------------------------------------------------------------------ voice step problems
def test_voice_timeout_then_finish_with_the_face_only(tmp_path):
    st = Station(tmp_path, audio=np.zeros(16000, np.float32))  # nobody reads
    st.start()
    st.wait_phase("voice_failed")
    assert st.rec[C.ENROLL_STATE][-1]["reason"] == "not enough speech"
    assert st.mic.stopped
    st.send("skip_voice")
    st.wait_closed()
    last = st.rec[C.ENROLL_STATE][-1]
    assert last["phase"] == "done" and last["face_ok"] and not last["voice_ok"]
    assert last["reason"] == "voice_skipped"
    assert not (st.people / "sam-1" / "voice.json").exists()


def test_silent_mic_says_it_did_not_start(tmp_path):
    st = Station(tmp_path, mic=FakeMic(np.zeros(1), dead=True))
    st.start()
    st.wait_phase("voice_failed")
    assert "didn't start" in st.rec[C.ENROLL_STATE][-1]["reason"]
    st.send("cancel")
    st.wait_closed()
    assert st.rec.phases()[-1] == "done"  # the face stays saved


def test_mic_open_error_keeps_the_voice_retry_available(tmp_path):
    class FlakyMic(FakeMic):
        starts = 0

        def start(self):
            self.starts += 1
            if self.starts == 1:
                raise OSError("device busy")
            self.stopped = False
            super().start()

    st = Station(tmp_path, mic=FlakyMic(speech_like(6.0)))
    st.start()
    st.wait_phase("voice_failed")
    assert "device busy" in st.rec[C.ENROLL_STATE][-1]["reason"]
    assert st.rec[C.ENROLL_STATE][-1]["face_ok"]
    st.send("retry")
    st.wait_closed()
    assert st.rec.phases()[-1] == "done"
    assert st.rec[C.ENROLL_STATE][-1]["voice_ok"]


class StoppingMic(FakeMic):
    """Delivers `blocks` blocks, then nothing (unplugged mid-sentence)."""

    def __init__(self, samples, blocks):
        super().__init__(samples)
        self.left = blocks

    def read(self, timeout=0.1):
        self.left -= 1
        if self.left < 0:
            time.sleep(min(timeout, 0.01))
            return None
        return super().read(timeout)


def test_a_mic_that_stops_mid_sentence_is_reported(tmp_path):
    st = Station(tmp_path, mic=StoppingMic(speech_like(6.0), blocks=50))  # 0.5 s, then gone
    st.start()
    st.wait_phase("voice_failed")
    assert "stopped" in st.rec[C.ENROLL_STATE][-1]["reason"]
    st.send("skip_voice")
    st.wait_closed()
    assert st.rec.phases()[-1] == "done"


def test_too_little_voice_for_cam_is_a_voice_failure(tmp_path):
    st = Station(tmp_path)

    def picky(audio):
        raise ValueError("not enough voice audio")

    st.enroller._extractor = picky
    st.start()
    st.wait_phase("voice_failed")
    assert st.rec[C.ENROLL_STATE][-1]["reason"] == "not enough speech"
    st.send("cancel")
    st.wait_closed()


# ------------------------------------------------------------------ requests
def test_consent_is_required(tmp_path):
    st = Station(tmp_path)
    st.enroller.command({"action": "start", "name": "Sam", "consent": False, "consent_t": 1.0})
    assert st.rec[C.ENROLL_STATE][-1]["phase"] == "cancelled"
    assert st.rec[C.ENROLL_STATE][-1]["reason"] == "consent is required"
    assert not st.cameras


def test_station_off_means_use_the_glasses(tmp_path):
    st = Station(tmp_path, enroll={"source": "glasses"})
    st.start()
    last = st.rec[C.ENROLL_STATE][-1]
    assert last["phase"] == "fallback" and last["reason"] == "station_off"
    assert not st.cameras


def test_a_new_save_replaces_the_running_one(tmp_path):
    st = Station(tmp_path, camera=lambda: FakeCamera(delay=0.05))
    st.start(client_id=1)
    st.wait_phase("face")
    first = st.enroller.session
    st.enroller._camera_factory = lambda: FakeCamera()  # the next one runs normally
    st.start(client_id=2, name="Ana")
    assert first.closed.wait(5)
    st.wait_closed()
    ends = [(e["session_id"], e["phase"], e.get("reason")) for e in st.rec[C.ENROLL_STATE]]
    assert (first.session_id, "cancelled", "replaced") in ends
    assert ends[-1][1] == "done" and st.saved[-1]["name"] == "Ana"
