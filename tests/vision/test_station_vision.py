"""V-23: the enrollment station inside vision.

- Camera clash: the station reserves the laptop camera while it holds it, so the glasses
  camera's fallback never fights it for the device; infrared cameras are never a fallback;
  the station never opens the glasses camera or mic; when the main camera already holds the
  laptop camera, the station shares its frames instead of opening it again.
- With the real face models on LFW photos: a save started from a glasses face checks that
  the station face is the same person, then names that glasses face at once; a different
  person is caught and can be saved as someone new without touching the glasses face.

Fake devices only: no camera or mic is opened.
"""

from __future__ import annotations

import json
import os
import threading
import time

import cv2
import numpy as np
import pytest
from attune.core import contracts as C
from attune.core.bus import Bus
from attune.station.session import StationEnroller, VisionHooks
from attune.station.sources import DeviceCamera, DeviceMic
from attune.vision import camera as camera_mod
from attune.vision import types as T
from attune.vision.camera import Camera, reserved
from attune.vision.service import VisionService

from .conftest import (
    ROOT,
    FakeBus,
    lfw_people,
    needs_face_models,
    needs_landmarker,
    needs_lfw,
)
from .station_fakes import (
    FakeCamera,
    FakeDetector,
    FakeEmbedder,
    FakeMic,
    energy_vad,
    person,
    speech_like,
    textured_frame,
)
from .test_camera_hotplug import BRIO, LAPTOP, FakeDevices

IR = "Integrated IR Camera"


def wait_for(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def devices(monkeypatch):
    fake = FakeDevices([BRIO, LAPTOP])
    monkeypatch.setattr(camera_mod, "list_cameras", fake.list_cameras)
    monkeypatch.setattr(camera_mod.cv2, "VideoCapture", fake.capture(lambda: fake.plugged))
    monkeypatch.setattr(Camera, "PREFERRED_RECHECK_S", 0.05)
    return fake


def station_camera(avoid=("Brio 101",)):
    return DeviceCamera("OV02E10", 1280, 720, 30, time.perf_counter, avoid=avoid)


# ------------------------------------------------------------------ camera clash
def test_the_glasses_camera_never_takes_the_laptop_camera_from_a_save(devices):
    main = Camera(name="Brio 101")
    main.start()
    station = None
    try:
        assert wait_for(lambda: main.connected and BRIO in devices.opened)
        station = station_camera()
        station.start()
        assert wait_for(lambda: LAPTOP in devices.opened and station.connected)
        assert LAPTOP in reserved()
        devices.plugged.remove(BRIO)  # the glasses webcam is unplugged mid-save
        assert wait_for(lambda: not main.connected)
        time.sleep(0.3)
        assert devices.opened.count(LAPTOP) == 1  # only the station has it
        station.stop()
        station = None
        assert wait_for(lambda: LAPTOP not in reserved())
        # the save is over: the glasses view falls back to the laptop camera as before
        assert wait_for(lambda: main.connected and devices.opened.count(LAPTOP) == 2)
    finally:
        if station is not None:
            station.stop()
        main.stop()


def test_an_infrared_camera_is_never_a_fallback(devices):
    devices.plugged[:] = [IR, LAPTOP]  # listed first, as Windows often does
    main = Camera(name="Brio 101")
    main.start()
    try:
        assert wait_for(lambda: main.connected)
        assert devices.opened == [LAPTOP] and main.device_name == LAPTOP
    finally:
        main.stop()
    named = Camera(name="IR Camera")  # asked for by name: allowed (no one does this)
    named.start()
    try:
        assert wait_for(lambda: named.connected) and named.device_name == IR
    finally:
        named.stop()


def test_the_station_never_opens_another_camera(devices):
    devices.plugged[:] = [BRIO, IR]  # no laptop camera
    station = station_camera()
    station.start()
    try:
        time.sleep(0.3)
        assert devices.opened == [] and not station.connected
    finally:
        station.stop()


class FakeSD:
    """sounddevice's device list; nothing is opened."""

    def __init__(self, devices):
        self.devices = devices

    def query_devices(self):
        return self.devices

    def query_hostapis(self):
        return [{"name": "MME"}, {"name": "Windows WASAPI"}]


def test_the_station_mic_is_the_named_laptop_mic_or_nothing(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    laptop = "Microphone Array on SoundWire Device (7- Realtek XU)"
    brio = "Microphone (Brio 101)"
    sd = FakeSD(
        [
            {"name": laptop, "hostapi": 0, "max_input_channels": 2},  # MME: skipped
            {"name": brio, "hostapi": 1, "max_input_channels": 1},
            {"name": "Speakers (Realtek)", "hostapi": 1, "max_input_channels": 0},
            {"name": laptop, "hostapi": 1, "max_input_channels": 2},
        ]
    )
    mic = DeviceMic("Microphone Array", time.perf_counter, sd=sd, avoid=("Brio 101",))
    assert mic.reader._device() == (3, True) and mic.label == laptop
    gone = DeviceMic("Microphone Array", time.perf_counter, sd=FakeSD(sd.devices[1:3]))
    with pytest.raises(RuntimeError, match="no input device"):
        gone.reader._device()  # never the default input (that is the glasses mic)
    # a name that would match the glasses mic is refused too
    greedy = DeviceMic("Microphone", time.perf_counter, sd=FakeSD(sd.devices[1:3]), avoid=("Brio",))
    with pytest.raises(RuntimeError):
        greedy.reader._device()


def test_the_station_shares_frames_when_the_main_camera_holds_the_laptop_camera(
    tmp_path, monkeypatch
):
    """Brio missing: vision fell back to the laptop camera; the station must not reopen it."""
    import attune.station.session as session_mod

    def no_open(*a, **k):
        raise AssertionError("the station opened the camera a second time")

    monkeypatch.setattr(session_mod, "DeviceCamera", no_open)
    taps: list = []
    stop = threading.Event()

    def main_camera_thread():  # the main camera's reader, delivering 30 fps of media time
        n = 0
        while not stop.is_set():
            n += 1
            tap = taps[-1] if taps else None
            if tap is not None:
                tap(n, 1000.0 + n / 30, textured_frame(n % 2))
            time.sleep(0.004)

    feeder = threading.Thread(target=main_camera_thread, daemon=True)
    feeder.start()
    bus = Bus()
    states: list = []
    bus.subscribe(C.ENROLL_STATE, states.append)
    cfg = {
        "engine": {"data_dir": str(tmp_path / "data")},
        "vision": {"camera_name": "Brio 101"},
        "voice": {"enroll_s": 1.0},
        "enroll": {"face_s": 1.0, "open_timeout_s": 1.0, "voice_timeout_s": 5.0},
    }
    saved = []
    hooks = VisionHooks(
        lambda: ("OV02E10", True),
        taps.append,
        lambda req: saved.append(req) or {"person_id": "p1"},
    )
    enroller = StationEnroller(
        bus,
        cfg,
        FakeDetector(),
        FakeEmbedder(person(5)),
        hooks,
        root=str(tmp_path),
        mic_factory=lambda: FakeMic(speech_like(4.0)),
        extractor=lambda audio: np.eye(1, 192, dtype=np.float32)[0],
        vad_factory=lambda: energy_vad,
    )
    try:
        enroller.command({"action": "start", "name": "Sam", "consent": True, "consent_t": 1.0})
        assert enroller.session.closed.wait(15)
    finally:
        stop.set()
        enroller.stop()
    face = [s for s in states if s["phase"] == "face"]
    assert face and face[0]["shared"] is True and face[0]["camera"] == "OV02E10"
    assert states[-1]["phase"] == "done" and saved
    assert taps[0] is not None and taps[-1] is None  # the tap is removed when the face is done


# ------------------------------------------------------------------ real face models
SCALE = 1.3


@pytest.fixture(scope="module")
def two_people():
    people = lfw_people(min_images=20)
    ranked = sorted(people.items(), key=lambda kv: -len(kv[1]))
    load = lambda paths: [cv2.imread(p) for p in paths]
    return {"A": load(ranked[0][1][:14]), "B": load(ranked[1][1][:10])}


def glasses_scene(photo, x=500):
    """The glasses view: one person in a 1080p frame (as tests/vision/test_service.py)."""
    photo = cv2.resize(photo, None, fx=SCALE, fy=SCALE)
    frame = np.full((1080, 1920, 3), (60, 70, 80), np.uint8)
    h, w = photo.shape[:2]
    frame[350 : 350 + h, x : x + w] = photo
    return frame


def laptop_scene(photo):
    """The laptop camera: the person sitting in front of it, face in the middle."""
    photo = cv2.resize(photo, None, fx=2.0, fy=2.0)
    frame = np.full((720, 1280, 3), (90, 100, 110), np.uint8)
    h, w = photo.shape[:2]
    y0, x0 = (720 - h) // 2, (1280 - w) // 2
    frame[y0 : y0 + h, x0 : x0 + w] = photo
    return frame


class Harness:
    """VisionService with its real models, plus a station fed LFW photos."""

    def __init__(self, tmp_path, station_photos):
        self.bus = FakeBus()
        data = tmp_path / "data"
        cfg = {
            "engine": {"data_dir": str(data)},
            "vision": {"people_dir": str(data / "people"), "camera_name": "Brio 101"},
            "voice": {"enroll_s": 1.5},
            "enroll": {"face_s": 1.5, "face_timeout_s": 20, "voice_timeout_s": 10},
        }
        self.people = data / "people"
        self.svc = VisionService(self.bus, cfg, root=ROOT)
        self.svc.load_models()
        self.svc.connect()
        frames = [laptop_scene(p) for p in station_photos]
        self.svc.station = StationEnroller(
            self.bus,
            cfg,
            self.svc.detector,
            self.svc.embedder,
            VisionHooks(
                self.svc._main_camera,
                self.svc._share_frames,
                self.svc._station_save_wait,
            ),
            root=ROOT,
            camera_factory=lambda: FakeCamera(frames, fps=15, delay=0.02),
            mic_factory=lambda: FakeMic(speech_like(6.0)),
            extractor=lambda audio: np.eye(1, 192, dtype=np.float32)[0],
            vad_factory=lambda: energy_vad,
        )
        self.t, self.n = 0.0, 0

    def glasses(self, seconds, photo):
        out = None
        for _ in range(max(1, int(seconds * 15))):
            self.t += 1 / 15
            self.n += 1
            out = self.svc.process_frame(self.n, self.t, glasses_scene(photo))
        return out

    def command(self, **args):
        self.bus.publish(T.COMMAND, {"name": "enroll.station", "args": args})

    def states(self):
        return self.bus.published[C.ENROLL_STATE]

    def run_until(self, photo, done, timeout=60.0):
        """Keep the glasses running (the vision thread saves the face) until done()."""
        end = time.monotonic() + timeout
        out = None
        while time.monotonic() < end:
            out = self.glasses(0.2, photo)
            if done():
                return out
        raise AssertionError([s["phase"] for s in self.states()])

    def main_face(self, out):
        return max(out.tracks, key=lambda tr: tr.face_px)


@needs_face_models
@needs_landmarker
@needs_lfw
def test_station_save_names_the_glasses_face_at_once(tmp_path, two_people):
    A = two_people["A"]
    h = Harness(tmp_path, A[1:13])
    try:
        out = h.glasses(1.5, A[0])
        a = h.main_face(out)
        assert a.status == "unknown"
        h.command(
            action="start",
            track_id=a.track_id,
            name="Alex",
            consent=True,
            consent_t=1790000000.0,
            request_id="save-1",
        )
        out = h.run_until(A[0], lambda: h.svc.station.session.closed.is_set())
    finally:
        h.svc.station.stop()
    phases = [s["phase"] for s in h.states()]
    assert "mismatch" not in phases and phases[-1] == "done", phases
    res = [r for r in h.bus.published[T.ENROLL_RESULT] if T.get(r, "part") == "face"][-1]
    assert res.ok and res.source == "station" and res.track_id == a.track_id
    pid = res.person_id
    assert h.bus.last(T.PERSON_CHANGED) == T.PersonChanged(pid, "Alex", "enrolled")
    # the glasses face is named straight away, without waiting for a re-check
    out = h.glasses(0.1, A[0])
    face = h.main_face(out)
    assert (face.track_id, face.name, face.status) == (a.track_id, "Alex", "enrolled")
    # prints only: the face prints and the voice print, no frames or audio
    files = sorted(p.name for p in (h.people / pid).iterdir())
    assert files == ["face.npy", "meta.json", "voice.json"], files
    assert json.loads((h.people / pid / "voice.json").read_text())["source"] == "station"
    assert 5 <= np.load(h.people / pid / "face.npy").shape[0] <= 8


@needs_face_models
@needs_landmarker
@needs_lfw
def test_someone_else_at_the_laptop_is_caught(tmp_path, two_people):
    A, B = two_people["A"], two_people["B"]
    h = Harness(tmp_path, B[:10])
    try:
        out = h.glasses(1.5, A[0])
        a = h.main_face(out)
        h.command(
            action="start",
            track_id=a.track_id,
            name="Alex",
            consent=True,
            consent_t=1790000000.0,
        )
        h.run_until(A[0], lambda: bool(h.bus.published[C.ENROLL_MISMATCH]))
        mismatch = h.bus.published[C.ENROLL_MISMATCH][-1]
        assert mismatch["track_id"] == a.track_id and mismatch["score"] < 0.3
        assert not h.bus.published[T.ENROLL_RESULT]  # nothing saved yet
        sid = mismatch["session_id"]
        h.command(action="new_person", session_id=sid)  # "Save as someone new"
        h.run_until(A[0], lambda: h.svc.station.session.closed.is_set())
    finally:
        h.svc.station.stop()
    assert h.states()[-1]["phase"] == "done"
    res = [r for r in h.bus.published[T.ENROLL_RESULT] if T.get(r, "part") == "face"][-1]
    assert res.ok and res.track_id is None  # not linked to the glasses face
    face = h.main_face(h.glasses(0.3, A[0]))
    assert face.track_id == a.track_id and face.name != "Alex"


# ------------------------------------------------------------------ a film reel as the laptop camera
REELS = f"{ROOT}/data/reels/film"


def reel_prints(detector, embedder, path, seconds):
    """Face prints of the biggest face at these times of a reel (the glasses' view of them)."""
    from attune.vision.embedder import align

    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    rows = []
    for s in seconds:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(s * fps))
        ok, frame = cap.read()
        dets = detector.detect(frame) if ok else []
        if dets:
            d = max(dets, key=lambda x: x.width)
            rows.append(embedder.embed([align(frame, d.kps)])[0])
    cap.release()
    return np.stack(rows)


@needs_face_models
@pytest.mark.skipif(
    not (os.path.isfile(f"{REELS}/grandpa.mp4") and os.path.isfile(f"{REELS}/mom_talk.mp4")),
    reason="film reels not downloaded",
)
@pytest.mark.parametrize(("glasses_reel", "same"), [("grandpa", True), ("mom_talk", False)])
def test_a_film_reel_as_the_laptop_camera(tmp_path, glasses_reel, same):
    """The station's real camera path (Camera on a video file) with the real face models."""
    from attune.vision.detector import FaceDetector
    from attune.vision.embedder import FaceEmbedder

    # on the CPU: this runs beside a live engine that owns the GPU
    detector = FaceDetector(f"{ROOT}/models/faces/buffalo_l/det_10g.onnx", 640, use_gpu=False)
    embedder = FaceEmbedder(f"{ROOT}/models/faces/buffalo_l/w600k_r50.onnx", use_gpu=False)
    glasses = reel_prints(detector, embedder, f"{REELS}/{glasses_reel}.mp4", (9.0, 10.5, 12.0))
    bus = Bus()
    states, previews, mismatches = [], [], []
    bus.subscribe(C.ENROLL_STATE, states.append)
    bus.subscribe(C.ENROLL_PREVIEW, previews.append)
    bus.subscribe(C.ENROLL_MISMATCH, mismatches.append)
    saved = []
    cfg = {
        "engine": {"data_dir": str(tmp_path / "data")},
        "vision": {"camera_name": "Brio 101"},
        "voice": {"enroll_s": 1.0},
        "enroll": {"face_s": 2.0, "camera_source": f"{REELS}/grandpa.mp4", "preview_fps": 8},
    }
    enroller = StationEnroller(
        bus,
        cfg,
        detector,
        embedder,
        VisionHooks(
            lambda: ("Brio 101", True),
            lambda tap: None,
            lambda r: saved.append(r) or {"person_id": "p1"},
        ),
        root=str(tmp_path),
        mic_factory=lambda: FakeMic(speech_like(3.0)),
        extractor=lambda audio: np.eye(1, 192, dtype=np.float32)[0],
        vad_factory=lambda: energy_vad,
    )
    try:
        enroller.command(
            {
                "action": "start",
                "name": "Grandpa",
                "consent": True,
                "consent_t": 1.0,
                "track_id": 3,
            },
            glasses,
        )
        end = time.monotonic() + 40
        while time.monotonic() < end and not enroller.session.closed.is_set():
            if mismatches:
                enroller.command({"action": "cancel"})
            time.sleep(0.05)
        assert enroller.session.closed.is_set(), [s["phase"] for s in states]
    finally:
        enroller.stop()
    face = next(s for s in states if s["phase"] == "face")
    assert face["camera"] == "grandpa.mp4" and face["shared"] is False
    jpeg = cv2.imdecode(np.frombuffer(previews[0]["jpeg"], np.uint8), cv2.IMREAD_COLOR)
    assert jpeg.shape[:2] == (480, 360)
    boxes = [p["face"] for p in previews if p["face"]]
    assert boxes and 0.25 < boxes[-1][0] + boxes[-1][2] / 2 < 0.75  # his face, mid-preview
    if same:
        assert not mismatches and states[-1]["phase"] == "done"
        assert saved[0]["track_id"] == 3 and 5 <= len(saved[0]["prints"]) <= 8
    else:
        assert mismatches and mismatches[0]["score"] < 0.35 and not saved
        assert states[-1]["phase"] == "cancelled"
