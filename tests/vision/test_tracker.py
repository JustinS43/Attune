"""V-04: tracker keeps IDs steady, survives short gaps, remembers exits, re-identifies."""

import numpy as np
from attune.vision.detector import Detection
from attune.vision.tracker import FaceTracker, iou_matrix

W = 1920


def det(x, y, size=120, score=0.9):
    box = np.array([x, y, x + size, y + size], dtype=float)
    return Detection(box=box, score=score, kps=np.zeros((5, 2)))


def test_iou():
    a = np.array([[0, 0, 10, 10]], float)
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]], float)
    assert np.allclose(iou_matrix(a, b), [[1.0, 1 / 3, 0.0]])


def test_ids_stay_steady_for_moving_faces():
    tr = FaceTracker()
    ids = None
    for i in range(60):  # 2 s at 30 fps, two faces moving 8 px per frame
        t = i / 30
        upd = tr.update([det(300 + 8 * i, 400), det(1400 - 8 * i, 380)], t, W)
        cur = sorted(x.track_id for x in upd.active)
        if ids is None:
            ids = cur
        assert cur == ids
    assert len(ids) == 2


def test_fast_motion_is_followed_by_prediction():
    tr = FaceTracker()
    first = tr.update([det(100, 400)], 0.0, W).active[0].track_id
    for i in range(1, 30):
        upd = tr.update([det(100 + 40 * i, 400)], i / 30, W)  # 1200 px/s
        assert [x.track_id for x in upd.active] == [first]


def test_short_gap_survives_long_gap_is_lost_with_side():
    tr = FaceTracker(survive_s=1.0)
    tid = tr.update([det(1850, 400, 60)], 0.0, W).active[0].track_id
    assert tr.update([], 0.5, W).lost == []
    assert (
        tr.update([det(1852, 400, 60)], 0.6, W).active[0].track_id == tid
    )  # back after 0.6 s gap
    upd = tr.update([], 1.8, W)
    assert [x.track_id for x in upd.lost] == [tid]
    assert upd.lost[0].exit_side == "right"
    assert tr.active == []


def test_exit_side_middle_and_left():
    tr = FaceTracker(survive_s=0.1)
    tr.update([det(900, 400), det(20, 400)], 0.0, W)
    upd = tr.update([], 0.5, W)
    assert sorted(x.exit_side for x in upd.lost) == ["left", "none"]


def test_low_score_detections_only_rescue_existing_tracks():
    tr = FaceTracker(det_score=0.5)
    assert (
        tr.update([det(500, 400, score=0.4)], 0.0, W).active == []
    )  # no new track from a weak box
    tid = tr.update([det(500, 400)], 0.1, W).active[0].track_id
    upd = tr.update([det(505, 400, score=0.35)], 0.2, W)
    assert upd.active[0].track_id == tid and upd.active[0].seen


def test_reidentify_returns_old_id_and_data():
    tr = FaceTracker(survive_s=0.2, lost_s=10.0, reid_threshold=0.5)
    emb = np.zeros(512, np.float32)
    emb[0] = 1.0
    old = tr.update([det(30, 400)], 0.0, W).active[0]
    old.embedding = emb
    old.data["name"] = "Maya"
    tr.update([], 1.0, W)
    new = tr.update([det(40, 420)], 3.0, W).new[0]
    assert new.track_id != old.track_id
    assert tr.reidentify(new, emb) is not None
    assert new.track_id == old.track_id and new.data["name"] == "Maya"


def test_lost_list_expires():
    tr = FaceTracker(survive_s=0.2, lost_s=10.0)
    old = tr.update([det(30, 400)], 0.0, W).active[0]
    old.embedding = np.ones(512, np.float32) / np.sqrt(512)
    tr.update([], 1.0, W)
    tr.update([], 12.0, W)
    assert tr.lost == []
