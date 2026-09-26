"""V-02 end to end: VisionService on a synthetic scene made from real LFW photos.

Two people stand in a 1080p frame. We check: both found and tracked, both
unknown at first, colour labels, enrollment with consent (and refusal without
it), the name appearing at once, leaving on the left and coming back with the
same track and name, recognition after a restart from the saved gallery (on
photos not used for enrollment), rename, delete, forget and pause.
"""

import os

import cv2
import numpy as np
import pytest
from attune.vision import types as T
from attune.vision.service import VisionService

from .conftest import (
    ROOT,
    FakeBus,
    lfw_people,
    needs_face_models,
    needs_landmarker,
    needs_lfw,
)

pytestmark = [needs_face_models, needs_landmarker, needs_lfw]
FPS = 15
SCALE = 1.3


@pytest.fixture(scope="module")
def two_people():
    people = lfw_people(min_images=20)
    ranked = sorted(people.items(), key=lambda kv: -len(kv[1]))
    (_, a_files), (_, b_files) = ranked[0], ranked[1]
    load = lambda paths: [
        cv2.resize(cv2.imread(p), None, fx=SCALE, fy=SCALE) for p in paths
    ]
    return {"A": load(a_files[:20]), "B": load(b_files[:3])}


def scene(people_at: dict[str, tuple[np.ndarray, int]]) -> np.ndarray:
    frame = np.full((1080, 1920, 3), (60, 70, 80), np.uint8)
    for photo, x in people_at.values():
        h, w = photo.shape[:2]
        x0 = max(x, 0)
        x1 = min(x + w, 1920)
        if x1 > x0:
            frame[350 : 350 + h, x0:x1] = photo[:, x0 - x : x1 - x]
    return frame


class Runner:
    def __init__(self, svc, bus):
        self.svc, self.bus, self.t, self.n = svc, bus, 0.0, 0

    def run(self, seconds, layout):
        """layout(i) -> {key: (photo, x)} for frame i of this stretch."""
        out = None
        for i in range(int(seconds * FPS)):
            self.t += 1 / FPS
            self.n += 1
            out = self.svc.process_frame(self.n, self.t, scene(layout(i)))
        return out


def make(tmp_path):
    bus = FakeBus()
    svc = VisionService(
        bus, {"vision": {"people_dir": str(tmp_path / "people")}}, root=ROOT
    )
    svc.load_models()
    svc.connect()
    return svc, bus, Runner(svc, bus)


def by_x(tracks, n=2):
    """The n main (largest) faces, left to right. Some LFW photos have a small face in the background."""
    main = sorted(tracks.tracks, key=lambda tr: -tr.face_px)[:n]
    return sorted(main, key=lambda tr: tr.box[0])


def test_vision_end_to_end(tmp_path, two_people):
    A, B = two_people["A"], two_people["B"]
    _, bus, run = make(tmp_path)

    # 1. Two strangers: found, tracked, unknown, colour-labelled after 1 s.
    out = run.run(2.0, lambda i: {"A": (A[0], 300), "B": (B[0], 1300)})
    a, b = by_x(out)
    assert (a.status, b.status) == ("unknown", "unknown")
    assert a.name is None and a.face_px >= 100
    ids = {a.track_id, b.track_id}
    assert all(
        {tr.track_id for tr in t.tracks} == ids
        for t in bus.published[T.VISION_TRACKS][3:]
    )
    colors = {ev.track_id: ev.color for ev in bus.published[T.VISION_APPEARANCE]}
    assert set(colors) == ids
    assert a.mouth_open is not None and a.lip_score < 0.015  # a photo doesn't talk

    # 2. Enrolling without consent is refused and stores nothing.
    bus.publish(
        T.COMMAND,
        {
            "name": "enroll.start",
            "args": {"track_id": a.track_id, "name": "Alex", "consent": False},
        },
    )
    run.run(0.2, lambda i: {"A": (A[0], 300), "B": (B[0], 1300)})
    assert bus.last(T.ENROLL_RESULT) == T.EnrollResult(
        None, "face", False, "consent is required", a.track_id
    )
    assert not os.path.exists(tmp_path / "people")

    # 3. Enrolling with consent: 5 s of varied photos, then the name shows at once.
    bus.publish(
        T.COMMAND,
        {
            "name": "enroll.start",
            "args": {
                "track_id": a.track_id,
                "name": "Alex",
                "consent": True,
                "consent_t": "2026-09-26T10:00:00",
            },
        },
    )
    out = run.run(5.4, lambda i: {"A": (A[1 + i % 8], 300), "B": (B[0], 1300)})
    res = bus.last(T.ENROLL_RESULT)
    assert res.ok and res.part == "face" and res.track_id == a.track_id, res
    pid = res.person_id
    assert bus.last(T.PERSON_CHANGED) == T.PersonChanged(pid, "Alex", "enrolled")
    a, b = by_x(out)
    assert (a.name, a.status, a.person_id) == ("Alex", "enrolled", pid)
    assert b.status == "unknown"
    saved = np.load(tmp_path / "people" / pid / "face.npy")
    assert saved.shape == (8, 512)

    # 4. Alex walks off the left edge and comes back 3 s later: same track, same name.
    # (Settle first: swapping photos instantly can leave a background face from the
    # previous photo right where Alex's face appears, which real people never do.)
    a, _ = by_x(run.run(1.5, lambda i: {"A": (A[0], 300), "B": (B[0], 1300)}))
    assert a.name == "Alex"
    run.run(0.5, lambda i: {"A": (A[0], 300 - 60 * i), "B": (B[0], 1300)})
    run.run(1.5, lambda i: {"B": (B[0], 1300)})
    lost = [
        ev for ev in bus.published[T.VISION_TRACK_LOST] if ev.track_id == a.track_id
    ]
    assert lost and lost[-1].side == "left"
    run.run(1.5, lambda i: {"B": (B[0], 1300)})
    out = run.run(1.0, lambda i: {"A": (A[12], 700), "B": (B[0], 1300)})
    back = by_x(out)[0]
    assert back.track_id == a.track_id and back.name == "Alex"

    # 5. Pause: no faces reported.
    bus.publish(T.PAUSED, {"paused": True})
    assert run.run(0.2, lambda i: {"A": (A[12], 700)}).tracks == []
    bus.publish(T.PAUSED, {"paused": False})


def test_recognition_after_restart_on_unseen_photos(tmp_path, two_people):
    A, B = two_people["A"], two_people["B"]
    svc, bus, run = make(tmp_path)
    run.run(1.0, lambda i: {"A": (A[0], 300)})
    tid = svc.tracker.active[0].track_id
    bus.publish(
        T.COMMAND,
        {
            "name": "enroll.start",
            "args": {
                "track_id": tid,
                "name": "Alex",
                "consent": True,
                "consent_t": "2026-09-26T10:00:00",
            },
        },
    )
    run.run(5.4, lambda i: {"A": (A[i % 8], 300)})
    pid = bus.last(T.ENROLL_RESULT).person_id

    # A fresh engine loads the gallery from disk and sees photos it never enrolled.
    svc2, bus2, run2 = make(tmp_path)
    assert [p.name for p in svc2.gallery.people()] == ["Alex"]
    named_after = None
    for i in range(15):  # up to 1 s
        out = run2.run(
            1 / FPS, lambda _, i=i: {"A": (A[10 + (i // 5)], 400), "B": (B[1], 1300)}
        )
        a, b = by_x(out)
        if a.name == "Alex" and named_after is None:
            named_after = (i + 1) / FPS
    assert named_after is not None and named_after <= 1.0, "Alex not named within 1 s"
    assert b.status == "unknown" and b.name is None
    print(f"\nnamed after {named_after:.2f} s, match score {a.match_score:.2f}")

    # Rename, then delete: files gone and the tag goes back to unknown.
    bus2.publish(
        T.COMMAND,
        {"name": "person.rename", "args": {"person_id": pid, "name": "Alexandra"}},
    )
    a, _ = by_x(run2.run(0.2, lambda i: {"A": (A[10], 400), "B": (B[1], 1300)}))
    assert a.name == "Alexandra"
    bus2.publish(T.COMMAND, {"name": "person.delete", "args": {"person_id": pid}})
    a, _ = by_x(run2.run(0.2, lambda i: {"A": (A[10], 400), "B": (B[1], 1300)}))
    assert a.status == "unknown" and a.name is None
    assert not os.path.exists(tmp_path / "people" / pid)
    assert bus2.last(T.PERSON_CHANGED).action == "deleted"


def test_confirmed_introduction_names_a_stranger_for_the_session(tmp_path, two_people):
    B = two_people["B"]
    _, bus, run = make(tmp_path)
    out = run.run(1.0, lambda i: {"B": (B[0], 800)})
    tid = by_x(out, 1)[0].track_id
    bus.publish(
        T.NAME_PROPOSAL,
        {
            "proposal_id": "p1",
            "track_id": tid,
            "name": "Sam",
            "state": "proposed",
            "expires_t": run.t + 10,
        },
    )
    out = run.run(0.2, lambda i: {"B": (B[0], 800)})
    assert out.tracks[0].status == "proposed"
    bus.publish(
        T.NAME_PROPOSAL,
        {"proposal_id": "p1", "track_id": tid, "name": "Sam", "state": "confirmed"},
    )
    out = run.run(0.2, lambda i: {"B": (B[0], 800)})
    assert (out.tracks[0].name, out.tracks[0].status) == ("Sam", "named")
    assert not os.path.exists(tmp_path / "people")  # session names are never saved

    bus.publish(T.SESSION_FORGET, {})
    out = run.run(0.2, lambda i: {"B": (B[0], 800)})
    assert out.tracks[0].status == "unknown" and out.tracks[0].name is None


def test_small_enrollment_is_refused_with_a_reason(tmp_path, two_people):
    A = two_people["A"]
    tiny = [
        cv2.resize(p, None, fx=0.4, fy=0.4) for p in A[:8]
    ]  # faces ~55 px: found but too small to enroll
    _, bus, run = make(tmp_path)
    out = run.run(1.0, lambda i: {"A": (tiny[0], 800)})
    assert out.tracks, "a small face should still be detected"
    bus.publish(
        T.COMMAND,
        {
            "name": "enroll.start",
            "args": {
                "track_id": out.tracks[0].track_id,
                "name": "Alex",
                "consent": True,
                "consent_t": "t",
            },
        },
    )
    run.run(5.4, lambda i: {"A": (tiny[i % 8], 800)})
    res = bus.last(T.ENROLL_RESULT)
    assert not res.ok and res.reason == "come closer"
