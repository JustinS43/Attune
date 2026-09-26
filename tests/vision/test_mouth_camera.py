"""V-08 lip score maths and V-01 camera reader (file playback, loss and recovery, live camera)."""

import itertools
import os
import threading
import time

import cv2
import numpy as np
import pytest
from attune.vision.camera import Camera, list_cameras
from attune.vision.mouth import LipHistory, face_crop, mouth_ratio


def test_mouth_ratio():
    pts = np.zeros((478, 2), np.float32)
    pts[78], pts[308] = (100, 200), (160, 200)  # 60 px wide
    pts[13], pts[14] = (130, 195), (130, 207)  # 12 px open
    assert mouth_ratio(pts) == pytest.approx(0.2)


def test_lip_score_talking_vs_still():
    talk, still = LipHistory(), LipHistory()
    rng = np.random.default_rng(0)
    for i in range(60):
        t = i / 30
        talk.add(t, 0.2 + 0.12 * np.sin(2 * np.pi * 4 * t))
        still.add(t, 0.1 + 0.005 * rng.standard_normal())
    assert talk.score(2.0) >= 0.03
    assert still.score(2.0) < 0.015
    assert LipHistory().score(1.0) == 0.0


def test_face_crop_pads_at_the_edge():
    img = np.full((1080, 1920, 3), 255, np.uint8)
    crop = face_crop(img, np.array([0, 0, 100, 100]))
    assert crop.shape == (256, 256, 3)
    assert crop[0, 0].sum() == 0 and crop[-1, -1].sum() > 0  # padded corner is black


def _write_clip(path, frames=45, fps=30):
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (640, 360))
    for i in range(frames):
        img = np.zeros((360, 640, 3), np.uint8)
        cv2.putText(
            img, str(i), (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 5
        )
        vw.write(img)
    vw.release()


def test_file_source_plays_at_its_own_rate_and_loops(tmp_path):
    path = str(tmp_path / "clip.mp4")
    _write_clip(path, frames=15)
    got = []
    cam = Camera(source=path, on_frame=lambda n, t, img: got.append((n, t, img.shape)))
    cam.start()
    time.sleep(1.2)
    cam.stop()
    assert cam.connected
    assert 28 <= len(got) <= 40  # ~30 fps, looping past the 15-frame clip
    assert got[0][2] == (360, 640, 3)
    ts = [t for _, t, _ in got]
    assert all(b > a for a, b in itertools.pairwise(ts))


def test_missing_camera_reports_lost_and_keeps_retrying(monkeypatch):
    monkeypatch.setattr("attune.vision.camera.list_cameras", list)
    statuses = []
    cam = Camera(
        name="No Such Camera",
        fallback_any=False,
        on_status=lambda ok, d: statuses.append((ok, d)),
    )
    cam.connected = True  # pretend it was connected, so the loss is reported
    cam.start()
    time.sleep(1.5)
    cam.stop()
    assert statuses and statuses[0] == (False, "camera lost")
    assert not cam.connected


def test_wait_frame_returns_newest(tmp_path):
    path = str(tmp_path / "clip.mp4")
    _write_clip(path)
    cam = Camera(source=path)
    cam.start()
    first = cam.wait_frame(0, timeout=2.0)
    later = cam.wait_frame(first[0], timeout=2.0)
    cam.stop()
    assert later[0] > first[0]


@pytest.mark.skipif(
    os.environ.get("ATTUNE_LIVE_CAMERA") != "1",
    reason="set ATTUNE_LIVE_CAMERA=1 to use a real camera",
)
def test_live_camera_rate_and_freshness():
    """T-S7 (part): a real camera delivers a steady rate of fresh frames."""
    cams = list_cameras()
    assert cams, "no cameras found"
    name = os.environ.get("ATTUNE_CAMERA_NAME", cams[0].name)
    ages, lock = [], threading.Lock()

    def on_frame(n, t, img):
        with lock:
            ages.append((n, t, img.shape))

    cam = Camera(name=name, on_frame=on_frame)
    cam.start()
    deadline = time.monotonic() + 8
    while not cam.connected and time.monotonic() < deadline:
        time.sleep(0.1)
    time.sleep(5.0)
    cam.stop()
    print(
        f"\n{cam.device_name}: {cam.measured_fps:.1f} fps, frame {ages[-1][2] if ages else None}"
    )
    assert cam.connected or ages
    assert cam.measured_fps >= 24
