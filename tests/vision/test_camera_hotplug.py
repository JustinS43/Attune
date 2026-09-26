"""Camera hot-plug: fall back when the named camera goes, return when it's back.

Uses a fake camera enumeration and a fake VideoCapture, so no device is opened.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from attune.vision import camera as camera_mod
from attune.vision.camera import Camera, CameraInfo

BRIO, LAPTOP = "Logitech Brio 101", "OV02E10"


class FakeDevices:
    def __init__(self, names):
        self.plugged = list(names)
        self.opened: list[str] = []

    def list_cameras(self):
        return [CameraInfo(i, 0, name) for i, name in enumerate(self.plugged)]

    def capture(self, devices_at_open):
        fake = self

        class FakeCapture:
            def __init__(self, index, backend=None):
                self.name = devices_at_open()[index]
                fake.opened.append(self.name)

            def isOpened(self):
                return True

            def set(self, *args):
                return True

            def get(self, prop):
                return {camera_mod.cv2.CAP_PROP_FRAME_WIDTH: 640}.get(prop, 480)

            def read(self):
                time.sleep(0.01)
                if self.name not in fake.plugged:
                    return False, None
                return True, np.zeros((4, 4, 3), np.uint8)

            def release(self):
                pass

        return FakeCapture


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate()


@pytest.fixture
def devices(monkeypatch):
    fake = FakeDevices([BRIO, LAPTOP])
    monkeypatch.setattr(camera_mod, "list_cameras", fake.list_cameras)
    monkeypatch.setattr(camera_mod.cv2, "VideoCapture", fake.capture(lambda: fake.plugged))
    monkeypatch.setattr(Camera, "PREFERRED_RECHECK_S", 0.05)
    return fake


def test_camera_falls_back_and_returns_to_the_named_camera(devices):
    statuses = []
    cam = Camera(name="Brio 101", on_status=lambda ok, d: statuses.append((ok, d)))
    cam.start()
    try:
        wait_for(lambda: cam.connected and cam.device_name == BRIO)
        devices.plugged.remove(BRIO)
        wait_for(lambda: cam.connected and cam.device_name == LAPTOP)
        assert cam.on_fallback
        frames = cam.frame_no
        wait_for(lambda: cam.frame_no > frames + 5)  # still reading the fallback
        assert devices.opened == [BRIO, LAPTOP]

        devices.plugged.insert(0, BRIO)
        wait_for(lambda: cam.device_name == BRIO)
        assert not cam.on_fallback
        frames = cam.frame_no
        wait_for(lambda: cam.frame_no > frames + 5)
    finally:
        cam.stop()
    assert devices.opened == [BRIO, LAPTOP, BRIO]
    assert statuses[0] == (True, f"{BRIO} 640x480")
    assert (False, "camera lost") in statuses
    assert statuses[-1] == (True, f"{BRIO} 640x480")  # the switch back is reported


def test_named_camera_is_not_rechecked(devices, monkeypatch):
    calls = []
    real = devices.list_cameras
    monkeypatch.setattr(camera_mod, "list_cameras", lambda: calls.append(1) or real())
    cam = Camera(name="Brio 101")
    cam.start()
    try:
        wait_for(lambda: cam.connected)
        time.sleep(0.3)
    finally:
        cam.stop()
    assert len(calls) == 1  # only the open; no enumeration while on the named camera
