"""A-21: harvests wait for their audio and carry how many faces were talking."""

from __future__ import annotations

from attune.fusion.asd_gate import _Face
from attune.fusion.harvest import DelayedHarvests, TalkerLog
from attune.fusion.service import FusionService
from attune.fusion.speaker import _TrackInfo
from attune.vision.types import VoiceHarvest

from .conftest import FakeBus


def test_talker_log_keeps_the_most_faces_talking_at_once():
    log = TalkerLog(keep_s=10.0)
    for t, n in [(0.0, 1), (0.5, 2), (1.0, 1), (2.0, 0)]:
        log.note(t, n)
    assert log.most_between(0.0, 1.0) == 2
    assert log.most_between(0.9, 2.0) == 1
    assert log.most_between(5.0, 6.0) == 1  # nothing logged: one talker (the harvested face)
    log.note(20.0, 1)  # older ticks fall out
    assert log.most_between(0.0, 1.0) == 1
    log.clear()
    assert log.most_between(19.0, 21.0) == 1


def test_a_harvest_waits_for_its_audio_then_leaves_with_its_talkers():
    log = TalkerLog()
    for i in range(30):
        log.note(i / 15, 2 if 10 <= i < 12 else 1)
    delayed = DelayedHarvests(lag_s=0.4)
    delayed.add(VoiceHarvest("sam-1", 0.0, 1.6))
    delayed.add(VoiceHarvest("track-3", 1.7, 1.9))
    assert delayed.due(1.7, log) == []  # 0.1 s after the span: its audio may not be in yet
    out = delayed.due(2.1, log)
    assert [(h.person_id, h.talkers) for h in out] == [("sam-1", 2)]
    out = delayed.due(2.4, log)
    assert [(h.person_id, h.talkers) for h in out] == [("track-3", 1)]
    delayed.add(VoiceHarvest("sam-1", 3.0, 4.6))
    delayed.clear()  # forget session
    assert delayed.due(10.0, log) == []


def track(tid, t, talking=False, probable=False):
    info = _TrackInfo(tid, [], 0.0, None, None, "unknown", t, t)
    info.talking, info.probable = talking, probable
    return info


def test_fusion_counts_talking_faces_with_light_asd_where_it_is_fresh():
    svc = FusionService(FakeBus(), {"voice": {"harvest_lag_s": 0.3}})
    assert svc.harvests.lag_s == 0.3
    f = svc.fusion
    now = 10.0
    f.tracks = {
        1: track(1, now, talking=True),
        2: track(2, now, probable=True),
        3: track(3, now),
        4: track(4, now - 2.0, talking=True),  # gone from view: not counted
    }
    assert svc.talking_faces(now) == 2
    # Light-ASD scores face 3 as talking and face 1 as not: its verdict wins where fresh
    f.asd_gate.faces = {3: _Face(1.5, now, True), 1: _Face(-1.0, now, False)}
    assert svc.talking_faces(now) == 2  # faces 2 (lips) and 3 (Light-ASD)
    svc._on_forget({})
    assert svc.talkers.most_between(0, 100) == 1
