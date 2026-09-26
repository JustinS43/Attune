"""V-03, V-05, V-08 on real photos (LFW): finding faces, telling people apart, reading lips.

These use the downloaded models and data/testsets/lfw; they skip if either is missing.
"""

import itertools
import time

import cv2
import numpy as np
import pytest
from attune.vision.detector import FaceDetector
from attune.vision.embedder import FaceEmbedder, align, crop_quality, yaw_ratio
from attune.vision.mouth import MouthMeter

from .conftest import (
    DET_MODEL,
    LANDMARKER,
    REC_MODEL,
    lfw_people,
    needs_face_models,
    needs_landmarker,
    needs_lfw,
)

pytestmark = [needs_face_models, needs_lfw]


@pytest.fixture(scope="module")
def detector():
    return FaceDetector(DET_MODEL, det_size=640, score=0.3, min_face_px=36)


@pytest.fixture(scope="module")
def embedder():
    return FaceEmbedder(REC_MODEL)


@pytest.fixture(scope="module")
def people():
    return lfw_people(min_images=4, limit=40)


def main_face(detector, img):
    dets = [d for d in detector.detect(img) if d.score >= 0.5]
    if not dets:
        return None
    h, w = img.shape[:2]  # LFW centres the named person
    return min(
        dets,
        key=lambda d: np.hypot(
            (d.box[0] + d.box[2]) / 2 - w / 2, (d.box[1] + d.box[3]) / 2 - h / 2
        ),
    )


def test_models_run_on_the_gpu(detector, embedder):
    assert "CUDAExecutionProvider" in detector.providers, detector.providers
    assert "CUDAExecutionProvider" in embedder.session.get_providers()


def test_finds_faces_in_lfw(detector, people):
    paths = [p for files in people.values() for p in files[:2]]
    found = sum(main_face(detector, cv2.imread(p)) is not None for p in paths)
    assert found / len(paths) >= 0.98, f"{found}/{len(paths)}"


def test_landmarks_are_sensible(detector, people):
    img = cv2.imread(next(iter(people.values()))[0])
    d = main_face(detector, img)
    le, re, nose, lm, rm = d.kps
    assert le[0] < re[0] and lm[0] < rm[0]
    assert le[1] < nose[1] < lm[1]
    assert abs(yaw_ratio(d.kps)) < 0.5


def test_no_faces_in_a_blank_frame(detector):
    assert detector.detect(np.full((1080, 1920, 3), 90, np.uint8)) == []


def test_same_person_scores_high_different_people_low(detector, embedder, people):
    prints = {}
    for name, files in people.items():
        crops = []
        for p in files[:4]:
            img = cv2.imread(p)
            d = main_face(detector, img)
            if d is not None:
                crops.append(align(img, d.kps))
        if len(crops) >= 2:
            prints[name] = embedder.embed(crops)
    genuine = [
        float(a @ b) for e in prints.values() for a, b in itertools.combinations(e, 2)
    ]
    names = list(prints)
    impostor = [
        float(prints[x][0] @ prints[y][0]) for x, y in itertools.combinations(names, 2)
    ]
    genuine, impostor = np.array(genuine), np.array(impostor)
    print(
        f"\ngenuine: mean {genuine.mean():.3f}, 5th pct {np.percentile(genuine, 5):.3f}; "
        f"impostor: mean {impostor.mean():.3f}, max {impostor.max():.3f}; "
        f"above 0.45: genuine {np.mean(genuine >= 0.45):.1%}, impostor {np.mean(impostor >= 0.45):.2%}"
    )
    assert np.mean(genuine >= 0.45) >= 0.90
    assert np.mean(impostor >= 0.45) <= 0.002


def test_enrolled_gallery_names_the_right_person(detector, embedder, people):
    """Enroll 3 photos per person, then identify their other photos with the plan's rules (0.45 / 0.08)."""
    from attune.vision.gallery import Gallery

    g = Gallery("unused")
    owner, probes = {}, []
    for name, files in people.items():
        embs = []
        for p in files:
            img = cv2.imread(p)
            d = main_face(detector, img)
            if d is not None:
                embs.append(embedder.embed([align(img, d.kps)])[0])
        if len(embs) < 4:
            continue
        owner[g.add_session(name, np.stack(embs[:3])).person_id] = name
        probes += [(name, e) for e in embs[3:]]
    right = wrong = 0
    for name, e in probes:
        pid, score, second = g.match(e)
        if score >= 0.45 and score - second >= 0.08:
            right += owner[pid] == name
            wrong += owner[pid] != name
    print(
        f"\n{len(probes)} probes: named right {right}, named wrong {wrong}, left unknown {len(probes) - right - wrong}"
    )
    assert wrong == 0
    assert right / len(probes) >= 0.85


def test_quality_gate_rejects_small_and_blurry(detector, people):
    img = cv2.imread(next(iter(people.values()))[0])
    d = main_face(detector, img)
    ok = crop_quality(d, align(img, d.kps))
    assert ok.ok, ok
    small = cv2.resize(img, None, fx=0.45, fy=0.45)  # face about 50 px wide
    ds = max(detector.detect(small), key=lambda d: d.score)
    assert crop_quality(ds, align(small, ds.kps)).reason == "small"
    blurry = cv2.GaussianBlur(img, (0, 0), 4)
    db = main_face(detector, blurry)
    assert crop_quality(db, align(blurry, db.kps)).reason == "blurry"
    dark = (img * 0.15).astype(np.uint8)
    dd = main_face(detector, dark)
    if dd is not None:
        assert crop_quality(dd, align(dark, dd.kps)).reason == "dark"


def on_battery() -> bool:
    """True when a Windows laptop is running on battery (the GPU clocks down)."""
    import ctypes
    import sys

    if sys.platform != "win32":
        return False

    class Status(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_byte),
            ("BatteryFlag", ctypes.c_byte),
            ("BatteryLifePercent", ctypes.c_byte),
            ("SystemStatusFlag", ctypes.c_byte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    st = Status()
    return (
        bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)))
        and st.ACLineStatus == 0
    )


def test_speed_on_a_1080p_frame(detector, embedder, people):
    frame = np.full((1080, 1920, 3), 120, np.uint8)
    files = [f[0] for f in list(people.values())[:4]]
    for i, p in enumerate(files):
        frame[300:550, 200 + 420 * i : 450 + 420 * i] = cv2.imread(p)
    det960 = FaceDetector(DET_MODEL, det_size=960, score=0.3)
    for _ in range(3):
        det960.detect(frame)
    t0 = time.perf_counter()
    for _ in range(20):
        dets = det960.detect(frame)
    det_ms = (time.perf_counter() - t0) / 20 * 1000
    crops = [align(frame, d.kps) for d in dets]
    embedder.embed(crops)
    t0 = time.perf_counter()
    for _ in range(20):
        embedder.embed(crops)
    rec_ms = (time.perf_counter() - t0) / 20 * 1000
    battery = on_battery()
    print(
        f"\n1080p, det size 960: {len(dets)} faces, detect {det_ms:.1f} ms, {len(crops)} face prints "
        f"{rec_ms:.1f} ms ({'on battery' if battery else 'plugged in'})"
    )
    assert len(dets) == 4
    # Plan budget is 15 ms with the charger in; the GPU clocks down sharply on battery.
    assert det_ms <= (30.0 if battery else 15.0)
    assert rec_ms / len(crops) <= (6.0 if battery else 3.0)


@needs_landmarker
def test_mouth_ratio_on_real_faces(detector, people):
    meter = MouthMeter(LANDMARKER)
    ratios = []
    for files in list(people.values())[:10]:
        img = cv2.imread(files[0])
        d = main_face(detector, img)
        r = meter.measure(img, d.box)
        assert r is not None
        ratios.append(r)
    meter.close()
    assert all(0.0 <= r < 0.8 for r in ratios)
    print(f"\nmouth-open ratios: {np.round(ratios, 3)}")
