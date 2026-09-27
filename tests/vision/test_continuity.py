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


def test_light_asd_silence_longer_than_a_dip_is_its_verdict():
    sim = Sim()
    assert _talk(sim, 2.0).kind == "face"
    silent = {1: (500, 0.0, False, -3.0)}  # Light-ASD: clearly not talking
    assert sim.run(1.0, silent).kind == "face"  # a dip (asd_continuity_s): kept
    assert sim.run(1.0, silent).kind == "someone"  # longer: someone else took over
    sim = Sim(asd_continuity_s=0.0)  # off: Light-ASD's verdict at once
    _talk(sim, 2.0)
    assert sim.run(1.0, silent).kind == "someone"


# ---------------------------------------------------------------- a hand by the mouth (V-31)
def _asd_talk(sim, seconds, tid=1, x=500, score=2.5, **kw):
    return sim.run(seconds, {tid: (x, 0.05, True, score)}, **kw)


def _hand(sim, seconds, tid=1, x=500, score=-2.0, **kw):
    # a hand rests by the mouth: the lips read as still and Light-ASD's score drops
    return sim.run(seconds, {tid: (x, 0.0, False, score)}, **kw)


def _caption_speakers(sim, words):
    """Send one final transcript and tick until its captions come out."""
    sim.f.on_transcript(
        {"utt_id": "u9", "t_start": words[0][1], "t_end": words[-1][2], "final": True,
         "text": " ".join(w[0] for w in words), "words": words},
        sim.t,
    )  # fmt: skip
    for _ in range(20):
        _, caps, _ = sim.step({1: (500, 0.0, False, -3.0)}, speech=False)
        if caps:
            return [(c.speaker.kind, c.speaker.track_id, c.text) for c in caps]
    return []


def test_one_talking_face_keeps_its_words_through_light_asd_dips():
    """The live problem: one person talks to the laptop camera with a hand resting by
    their cheek. Their face is tracked, but Light-ASD's score dips below asd_off now and
    then while they talk on; each dip sent those words to the lower "Someone" caption."""
    sim = Sim()
    t0 = sim.t
    assert _asd_talk(sim, 2.0).kind == "face"
    for _ in range(3):  # talking, with a hand by the mouth for a second at a time
        spk = _hand(sim, 1.0)
        assert spk.kind == "face" and spk.track_id == 1
        assert _asd_talk(sim, 1.0).kind == "face"
    words = [(f"w{i}", t0 + 0.1 + i * 0.4, t0 + 0.4 + i * 0.4) for i in range(19)]
    assert _caption_speakers(sim, words) == [("face", 1, " ".join(w[0] for w in words))]


def test_an_offscreen_voice_learnt_from_a_hidden_mouth_does_not_veto_its_face():
    # the person started talking behind their hand: nobody seemed to talk, so their
    # voice was learnt as "offscreen-1". Now Light-ASD hears their face: it is theirs.
    sim = Sim()
    spk = _asd_talk(sim, 1.0, voice="offscreen-1")
    assert spk.kind == "face" and spk.track_id == 1
    _asd_talk(sim, 3.0, voice="offscreen-1")  # talking with that voice: it claims it
    assert sim.f.claimed.get("offscreen-1") == "track-1"
    spk = _hand(sim, 2.5, voice="offscreen-1")  # a longer dip: its own voice now supports it
    assert spk.kind == "face" and spk.track_id == 1


def test_a_talker_behind_their_hand_is_not_learnt_as_an_offscreen_voice():
    sim = Sim()
    _asd_talk(sim, 2.0)
    for _ in range(int(4.0 * 30)):  # talks on behind the hand, well past the dip bridge
        _, _, harvest = sim.step({1: (500, 0.0, False, -2.0)})
        assert harvest is None or not harvest.person_id.startswith("offscreen-")


# ---------------------------------------------------------------- automatic contacts (V-31)
def test_an_automatic_contacts_voice_does_not_veto_their_face_on_a_new_track():
    # auto-a's print was saved from track 1; they come back as track 7, not recognised yet
    sim = Sim()
    spk = _asd_talk(sim, 2.0, tid=7, voice="auto-a")
    assert spk.kind == "face" and spk.track_id == 7
    # ...nor when they are recognised as another automatic profile of the same person
    sim = Sim(people={7: ("auto-b", "New person")})
    spk = _asd_talk(sim, 2.0, tid=7, voice="auto-a")
    assert spk.kind == "face" and spk.track_id == 7


def test_an_automatic_contact_in_view_on_another_face_still_vetoes():
    sim = Sim(people={2: ("auto-a", "New person")})
    faces = {1: (300, 0.05, True, 2.5), 2: (900, 0.05, True, 2.5)}
    spk = sim.run(2.0, faces, voice="auto-a")
    assert spk.kind == "face" and spk.track_id == 2


def test_a_face_keeps_its_voice_when_it_is_recognised():
    # its session print is "track-1" (harvested before it had a name); now it is auto-a
    sim = Sim(people={1: ("auto-a", "New person")})
    spk = sim.run(2.0, {1: (500, 0.0, False, None)}, voice="track-1")
    assert sim.f._voice_verdict(sim.f.tracks[1], sim.t) == "support"
    assert spk.kind != "offscreen"


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


# ---------------------------------------------------------------- change at the pause
def test_a_speaker_change_snaps_back_to_the_pause_before_the_reply():
    from attune.fusion.speaker import SpeakerFusion, _Group
    from attune.vision.settings import FusionSettings
    from attune.vision.types import Speaker

    f = SpeakerFusion(FusionSettings())
    a, b = Speaker("face", 1, None, "A"), Speaker("face", 2, None, "B")
    # A: "I don't like it" | pause 0.4 s | B: "yeah well" (credited to A: late evidence) "you know"
    words_a = [("I", 0.0, 0.2), ("don't", 0.2, 0.4), ("like", 0.4, 0.6), ("it.", 0.6, 0.8),
               ("Yeah,", 1.2, 1.4), ("well,", 1.4, 1.6)]  # fmt: skip
    words_b = [("you", 1.62, 1.8), ("know", 1.8, 2.0)]
    groups = f._snap(
        [_Group(a, list(words_a), evidence=[a] * 6), _Group(b, list(words_b), evidence=[b] * 2)]
    )
    assert [w[0] for w in groups[0].words] == ["I", "don't", "like", "it."]
    assert [w[0] for w in groups[1].words] == ["Yeah,", "well,", "you", "know"]
    assert len(groups[1].evidence) == 4


def test_no_pause_no_snap_and_someone_is_never_given_words():
    from attune.fusion.speaker import SpeakerFusion, _Group
    from attune.vision.settings import FusionSettings
    from attune.vision.types import Speaker

    f = SpeakerFusion(FusionSettings())
    a, b = Speaker("face", 1, None, "A"), Speaker("face", 2, None, "B")
    even = [("one", 0.0, 0.3), ("two", 0.3, 0.6), ("three", 0.6, 0.9)]
    g = f._snap(
        [_Group(a, list(even), evidence=[a] * 3), _Group(b, [("four", 0.9, 1.2)], evidence=[b])]
    )
    assert len(g[0].words) == 3
    someone = Speaker("someone", label="Someone")
    paused = [("one", 0.0, 0.3), ("two", 0.8, 1.0)]
    g = f._snap(
        [
            _Group(a, list(paused), evidence=[a] * 2),
            _Group(someone, [("x", 1.1, 1.3)], evidence=[someone]),
        ]
    )
    assert len(g[0].words) == 2
