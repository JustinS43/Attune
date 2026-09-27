"""Continuity: a talker keeps their speech through a dip in the evidence (fusion `_continues`).

On podcast clips (scripts/eval_podcast.py) 60-70% of the words shown as "Someone" came
while the person on screen was plainly still talking: a hand or a cup over the mouth, a
head turn, Light-ASD without a fresh score on a busy laptop. The negative case, a silent
listener whose lips lined up with an off-camera voice for a moment, is
tests/vision/test_live_talker.py.
"""

from .test_asd import Sim


def _talk(sim, seconds, tid=1, x=500, **kw):
    return sim.run(seconds, {tid: (x, 0.05, True, None)}, **kw)


def _dip(sim, seconds, tid=1, x=500, **kw):
    # still talking (speech goes on), but the mouth evidence is gone: lips read as still
    return sim.run(seconds, {tid: (x, 0.0, False, None)}, **kw)


def test_talker_keeps_the_speech_through_a_dip():
    sim = Sim()
    assert _talk(sim, 2.0).kind == "face"
    spk = _dip(sim, 1.5)
    assert spk.kind == "face" and spk.track_id == 1


def test_off_by_default_zero():
    sim = Sim(continuity_s=0.0)
    assert _talk(sim, 2.0).kind == "face"
    assert _dip(sim, 1.5).kind == "someone"


def test_continuity_ends_after_continuity_s():
    sim = Sim(continuity_s=1.0)
    assert _talk(sim, 2.0).kind == "face"
    assert _dip(sim, 2.0).kind == "someone"


def test_a_pause_ends_it():
    sim = Sim()
    assert _talk(sim, 2.0).kind == "face"
    sim.run(0.8, {1: (500, 0.0, False, None)}, speech=False)  # a turn can start here
    assert _dip(sim, 1.0).kind == "someone"


def test_a_moment_of_lips_does_not_earn_it():
    sim = Sim()
    sim.run(3.0, {1: (500, 0.0, False, None)})  # someone off camera talks
    sim.run(0.6, {1: (500, 0.05, True, None)})  # the listener's lips move for a moment
    assert _dip(sim, 1.0).kind != "face"


def test_light_asd_verdict_is_never_overruled():
    sim = Sim()
    assert _talk(sim, 2.0).kind == "face"
    spk = sim.run(1.0, {1: (500, 0.0, False, -3.0)})  # Light-ASD: clearly not talking
    assert spk.kind == "someone"


def test_another_talking_face_takes_over():
    sim = Sim()
    assert sim.run(2.0, {1: (300, 0.05, True, None), 2: (900, 0.0, False, None)}).track_id == 1
    spk = sim.run(1.5, {1: (300, 0.0, False, None), 2: (900, 0.05, True, None)})
    assert spk.kind == "face" and spk.track_id == 2


# ---------------------------------------------------------------- voices of faces in view
def _learn_voices(sim):
    """Two strangers in view; each talks alone first, so each has a session voice print."""
    sim.f.harvested.update({"track-1", "track-2"})


def test_a_voice_whose_face_is_in_view_is_never_off_screen():
    sim = Sim()
    _learn_voices(sim)
    faces = {1: (300, 0.0, False, None), 2: (900, 0.0, False, None)}  # no lip evidence
    spk = sim.run(2.0, faces, voice="track-2")
    assert spk.kind == "probable_face" and spk.track_id == 2


def test_a_stale_voice_does_not_veto_the_face_light_asd_hears():
    sim = Sim()
    _learn_voices(sim)
    # the voice window still hears face 1 (the last turn); Light-ASD: 1 silent, 2 talking
    faces = {1: (300, 0.0, False, -3.0), 2: (900, 0.05, True, 2.5)}
    spk = sim.run(1.5, faces, voice="track-1")
    assert spk.kind == "face" and spk.track_id == 2


def test_a_stale_voice_of_a_silent_face_in_view_is_someone():
    sim = Sim()
    _learn_voices(sim)
    faces = {1: (300, 0.0, False, -3.0), 2: (900, 0.0, False, -3.0)}
    spk = sim.run(2.0, faces, voice="track-1")
    assert spk.kind == "someone"
