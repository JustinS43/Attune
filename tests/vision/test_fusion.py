"""V-09, V-10, V-11: who's talking, in-time check, captions and harvesting, with simulated audio.

Section 2's events (audio.vad, audio.block, audio.transcript, audio.voice_match) are
simulated here exactly as docs/contracts.md describes them, so these run without a mic.
"""

import math

import numpy as np
from attune.fusion.harvest import Harvester
from attune.fusion.speaker import SpeakerFusion
from attune.fusion.sync import Envelope, in_time_score
from attune.vision.settings import FusionSettings
from attune.vision.types import Speaker, Track, Tracks

FPS = 30


def syllables(t, rate=4.0, phase=0.0):
    """A talking rhythm between 0 and 1 (about 4 syllables a second)."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * rate * t + phase)


class Sim:
    """Drives SpeakerFusion with simulated faces and audio at 30 fps."""

    def __init__(self, settings=None):
        self.f = SpeakerFusion(settings or FusionSettings())
        self.t = 0.0
        self.frame = 0

    def step(self, faces, speech=False, loud=None, dt=1 / FPS, sensors=None):
        """faces: {track_id: (x, mouth_open or None, lip_score, person_id, name, status)}"""
        self.t += dt
        self.frame += 1
        tracks = [
            Track(tid, [x, 400, 120, 120], 120, lip, pid, name, 0.0, status, mouth)
            for tid, (x, mouth, lip, pid, name, status) in faces.items()
        ]
        self.f.on_tracks(Tracks(self.frame, self.t, tracks))
        self.f.on_vad(
            {"t": self.t, "is_speech": speech, "prob": 0.9 if speech else 0.1}
        )
        if loud is not None:
            self.f.on_audio_level({"t": self.t, "db": loud})
        if sensors is not None:
            self.f.on_sensor_levels(
                {
                    "t": self.t,
                    "left": sensors[0],
                    "right": sensors[1],
                    "motor_on": False,
                }
            )
        return self.f.tick(self.t)


def talking_face(t, x=500, phase=0.0, pid=None, name=None, status="unknown"):
    return (x, 0.2 + 0.15 * syllables(t, phase=phase), 0.05, pid, name, status)


def still_face(x=1200, lip=0.003, pid=None, name=None, status="unknown"):
    return (x, 0.1, lip, pid, name, status)


# ---------------- V-10 in-time check ----------------
def test_in_time_high_for_lips_following_sound_low_for_chewing():
    env = Envelope()
    talk, chew = [], []
    for i in range(60):
        t = i / FPS
        env.add(t, -30 + 20 * syllables(t))
        talk.append((t, 0.2 + 0.15 * syllables(t)))
        chew.append(
            (t, 0.2 + 0.15 * syllables(t, rate=1.3, phase=1.0))
        )  # moving, but not with the sound
    now = 2.0
    assert in_time_score(talk, env, now) > 0.8
    assert in_time_score(chew, env, now) < 0.3


def test_in_time_allows_the_calibrated_offset():
    env = Envelope()
    lips = []
    for i in range(60):
        t = i / FPS
        env.add(
            t, -30 + 20 * syllables(t - 0.08)
        )  # sound arrives 80 ms after the lips move
        lips.append((t, 0.2 + 0.15 * syllables(t)))
    assert in_time_score(lips, env, 2.0, offset_s=0.08) > 0.9


def test_in_time_unknown_without_sound():
    lips = [(i / FPS, 0.2 + 0.1 * syllables(i / FPS)) for i in range(60)]
    assert in_time_score(lips, Envelope(), 2.0) is None


# ---------------- V-09 cases ----------------
def test_visible_speaker_with_lips_in_time():
    sim = Sim()
    for _ in range(45):
        scene, _, _ = sim.step(
            {1: talking_face(sim.t), 2: still_face()},
            speech=True,
            loud=-30 + 20 * syllables(sim.t),
        )
    spk = sim.f.current
    assert spk.kind == "face" and spk.track_id == 1
    assert [f.is_speaker for f in scene.faces] == [True, False]
    assert not scene.faces[0].dashed


def pcm_block(t, sample_rate=16000, ms=10):
    """A 10 ms block of speech-like noise whose loudness follows the syllable rhythm."""
    n = sample_rate * ms // 1000
    amp = 10 ** ((-40 + 30 * syllables(t)) / 20)
    rng = np.random.default_rng(int(t * 1000))
    samples = (amp * np.sqrt(2) * rng.standard_normal(n)).astype(np.float32)
    return {"t": t, "sample_rate": sample_rate, "samples": samples}


def test_loudness_envelope_from_audio_blocks():
    sim = Sim()
    sim.f.on_audio_block(
        {"t": 1.0, "sample_rate": 16000, "samples": np.full(320, 0.1, np.float32)}
    )
    t, db = sim.f.envelope.samples[-1]
    assert t == 1.0 and abs(db - (-20.0)) < 0.01
    sim.f.on_audio_block(
        {"t": 2.0, "sample_rate": 32000, "samples": np.full(640, 0.5, np.float32)}
    )
    assert sim.f.envelope.samples[-1][0] == 1.0  # the 32 kHz alert stream is ignored


def test_speaker_found_from_real_pcm_blocks():
    sim = Sim()
    block_t = 0.0
    for _ in range(45):
        while block_t < sim.t + 1 / FPS:  # the mic runs ahead in 10 ms blocks
            sim.f.on_audio_block(pcm_block(block_t))
            block_t += 0.01
        sim.step({1: still_face(x=300), 2: talking_face(sim.t, x=900)}, speech=True)
    assert sim.f.current.kind == "face" and sim.f.current.track_id == 2


def test_nodding_face_out_of_time_is_not_the_speaker():
    sim = Sim()
    for _ in range(45):
        nod = (
            500,
            0.2 + 0.15 * syllables(sim.t, rate=1.3, phase=1.0),
            0.05,
            None,
            None,
            "unknown",
        )
        sim.step({1: nod}, speech=True, loud=-30 + 20 * syllables(sim.t))
    assert sim.f.current.kind != "face"


def test_no_speech_means_no_speaker():
    sim = Sim()
    for _ in range(30):
        scene, _, _ = sim.step({1: talking_face(sim.t)}, speech=False)
    assert sim.f.current is None and not any(f.is_speaker for f in scene.faces)


def test_hold_and_switch_rule():
    s = FusionSettings()
    sim = Sim(s)
    loud = lambda: -30 + 20 * syllables(sim.t)
    a = lambda lip: (400, 0.2 + 0.15 * syllables(sim.t), lip, None, None, "unknown")
    b = lambda lip: (1300, 0.2 + 0.15 * syllables(sim.t), lip, None, None, "unknown")
    for _ in range(40):
        sim.step({1: a(0.05), 2: still_face(1300)}, speech=True, loud=loud())
    assert sim.f.current.track_id == 1
    # B only 1.2x higher: A keeps the bubble. (B starts in the middle of A's speech, so it
    # has to keep moving in time for talk_sustain_s before it counts as talking at all.)
    for _ in range(round(s.talk_sustain_s * FPS) + 15):
        sim.step({1: a(0.05), 2: b(0.06)}, speech=True, loud=loud())
    assert sim.f.current.track_id == 1
    sim.step({1: a(0.05), 2: b(0.08)}, speech=True, loud=loud())  # 1.6x higher: switch
    assert sim.f.current.track_id == 2


def test_probable_speaker_is_dashed():
    sim = Sim()
    for _ in range(20):  # no sound to check against: movement in the probable band
        scene, _, _ = sim.step(
            {1: (500, 0.2, 0.010, None, None, "unknown"), 2: still_face(lip=0.002)},
            speech=True,
        )
    assert sim.f.current.kind == "probable_face"
    assert scene.faces[0].is_speaker and scene.faces[0].dashed


def test_two_uncertain_faces_are_not_guessed():
    sim = Sim()
    for _ in range(20):
        sim.step(
            {
                1: (500, 0.2, 0.010, None, None, "unknown"),
                2: (1200, 0.2, 0.010, None, None, "unknown"),
            },
            speech=True,
            sensors=(300, 300),
        )
    assert sim.f.current.kind == "someone"


def test_you_from_both_sensors_loud_and_balanced():
    s = FusionSettings(you_level_db=50.0)
    sim = Sim(s)
    for _ in range(10):
        scene, _, _ = sim.step({2: still_face()}, speech=True, sensors=(700, 650))
    assert sim.f.current.kind == "you" and scene.you_speaking
    for _ in range(10):  # loud but lopsided: someone to the left, not you
        sim.step({2: still_face()}, speech=True, sensors=(700, 150))
    assert sim.f.current.kind == "someone" and sim.f.current.side == "left"


def test_offscreen_named_by_voice_with_exit_side():
    sim = Sim()
    for _ in range(10):
        sim.step({3: (40, 0.1, 0.005, "maya-1", "Maya", "enrolled")})
    sim.f.on_track_lost({"track_id": 3, "t": sim.t, "side": "left"})
    for _ in range(10):  # speech starts, nobody visible
        sim.step({}, speech=True, sensors=(200, 200))
    assert sim.f.current.kind == "someone"
    sim.f.on_voice_match({"utt_id": "u1", "person_id": "maya-1", "score": 0.7})
    for _ in range(30):
        scene, _, _ = sim.step({}, speech=True, sensors=(200, 200))
    spk = sim.f.current
    assert (spk.kind, spk.person_id, spk.label, spk.side) == (
        "offscreen",
        "maya-1",
        "Maya",
        "left",
    )
    assert scene.offscreen[0].label == "Maya" and scene.offscreen[0].side == "left"


def test_offscreen_side_from_louder_sensor_when_no_exit_known():
    sim = Sim()
    for _ in range(40):
        sim.step({}, speech=True, sensors=(150, 400))
    assert sim.f.current.side == "right"


def test_weak_voice_match_stays_someone():
    sim = Sim()
    for _ in range(5):
        sim.step({}, speech=True)
    sim.f.on_voice_match({"utt_id": "u1", "person_id": "maya-1", "score": 0.4})
    for _ in range(30):
        sim.step({}, speech=True)
    assert sim.f.current.kind == "someone"


# ---------------- captions ----------------
def test_caption_goes_to_the_speaker_and_splits_at_a_change():
    sim = Sim()
    loud = lambda: -30 + 20 * syllables(sim.t)
    for _ in range(40):
        sim.step(
            {1: talking_face(sim.t, 400), 2: still_face(1300)}, speech=True, loud=loud()
        )
    t_switch = sim.t
    for _ in range(40):
        sim.step(
            {1: still_face(400), 2: talking_face(sim.t, 1300)}, speech=True, loud=loud()
        )
    words = [
        ("So", t_switch - 1.0, t_switch - 0.7),
        ("what?", t_switch - 0.4, t_switch - 0.1),
        ("Robots", t_switch + 0.9, t_switch + 1.3),
        ("again.", t_switch + 1.4, t_switch + 1.8),
    ]
    sim.f.on_transcript(
        {
            "utt_id": "7",
            "t_start": words[0][1],
            "t_end": words[-1][2],
            "text": "So what? Robots again.",
            "final": True,
            "lang": "en",
            "words": words,
        },
        sim.t,
    )
    _, caps, _ = sim.step({1: still_face(400), 2: still_face(1300)}, speech=True)
    assert [(c.utt_id, c.speaker.track_id, c.text) for c in caps] == [
        ("7", 1, "So what?"),
        ("7.1", 2, "Robots again."),
    ]
    assert all(c.final for c in caps)


def test_first_words_wait_up_to_300ms_then_show_as_someone():
    sim = Sim()
    sim.step({}, speech=True)
    sim.f.on_transcript(
        {
            "utt_id": "9",
            "t_start": sim.t,
            "t_end": sim.t + 0.3,
            "text": "Hello",
            "final": False,
            "lang": "en",
            "words": [("Hello", sim.t, sim.t + 0.3)],
        },
        sim.t,
    )
    _, caps, _ = sim.step({}, speech=True, dt=0.1)
    assert caps == []
    _, caps, _ = sim.step({}, speech=True, dt=0.25)
    assert len(caps) == 1 and caps[0].speaker.kind == "someone"


# ---------------- labels ----------------
def test_labels_names_proposals_colours_and_duplicates():
    sim = Sim()
    sim.f.on_appearance({"track_id": 2, "color": "blue"})
    sim.f.on_appearance({"track_id": 3, "color": "blue"})
    sim.f.on_description({"track_id": 3, "label": "Person in blue jacket"})
    sim.f.on_appearance({"track_id": 4, "color": "blue"})
    sim.f.on_name_proposal({"track_id": 5, "name": "Sam", "state": "proposed"})
    scene, _, _ = sim.step(
        {
            1: still_face(100, pid="maya-1", name="Maya", status="enrolled"),
            2: still_face(400),
            3: still_face(700),
            4: still_face(1000),
            5: still_face(1300),
        }
    )
    labels = {f.track_id: f.label for f in scene.faces}
    assert labels == {
        1: "Maya",
        2: "Person in blue",
        3: "Person in blue jacket",
        4: "Person in blue, 2",
        5: "Sam?",
    }
    assert {f.track_id: f.status for f in scene.faces}[5] == "proposed"


# ---------------- V-11 harvesting ----------------
def test_harvester_emits_after_continuous_confident_speech():
    h = Harvester(1.5)
    assert h.update(0.0, "maya-1") is None
    assert h.update(1.0, "maya-1") is None
    out = h.update(1.6, "maya-1")
    assert (out.person_id, out.t0, out.t1) == ("maya-1", 0.0, 1.6)
    assert h.update(2.0, None) is None
    assert h.update(2.1, "maya-1") is None  # a break restarts the stretch


def test_fusion_harvests_a_confident_stranger_under_track_id():
    sim = Sim()
    harvests = []
    for _ in range(90):
        _, _, hv = sim.step(
            {1: talking_face(sim.t), 2: still_face()},
            speech=True,
            loud=-30 + 20 * syllables(sim.t),
        )
        if hv:
            harvests.append(hv)
    assert harvests and harvests[0].person_id == "track-1"
    assert harvests[0].t1 - harvests[0].t0 >= 1.5


def test_forget_session_clears_labels():
    sim = Sim()
    sim.f.on_appearance({"track_id": 2, "color": "red"})
    sim.f.forget_session()
    scene, _, _ = sim.step({2: still_face()})
    assert scene.faces[0].label == "Person"


def test_a_short_piece_joins_its_longer_neighbour():
    f = SpeakerFusion(FusionSettings())
    a = Speaker("face", 1, None, "Person in white shirt", "none")
    b = Speaker("face", 2, None, "Person in blue shirt", "none")
    words = [
        ("Hi,", 0.0, 0.3),
        ("my", 0.4, 0.5),
        ("name", 0.6, 0.9),
        ("is", 1.0, 1.1),
        ("Sam", 1.2, 1.5),
    ]
    who = [a, a, a, a, b]
    f.timeline.extend((w[1], s) for w, s in zip(words, who))
    caps = f._captions_for(
        {
            "utt_id": "3",
            "text": "Hi, my name is Sam",
            "final": True,
            "words": words,
            "t_start": 0.0,
            "t_end": 1.5,
        },
        now=5.0,
        first_seen=0.0,
    )
    assert [(c.speaker.track_id, c.text) for c in caps] == [(1, "Hi, my name is Sam")]


def test_a_flickering_speaker_does_not_chop_a_sentence():
    f = SpeakerFusion(FusionSettings())
    a = Speaker("face", 1, None, "Person in white shirt", "none")
    b = Speaker("face", 2, None, "Person in blue shirt", "none")
    someone = Speaker("someone", label="Someone", side="none")
    words = [
        ("Did", 0.0, 0.2),
        ("you", 0.3, 0.4),
        ("hear", 0.5, 0.7),
        ("the", 0.8, 0.9),
        ("doorbell", 1.0, 1.5),
        ("a", 1.6, 1.7),
        ("minute", 1.8, 2.0),
        ("ago", 2.1, 2.4),
    ]
    who = [someone, a, a, b, a, someone, someone, someone]
    f.timeline.extend((w[1], s) for w, s in zip(words, who))
    caps = f._captions_for(
        {
            "utt_id": "9",
            "text": "Did you hear the doorbell a minute ago",
            "final": True,
            "words": words,
            "t_start": 0.0,
            "t_end": 2.4,
        },
        now=5.0,
        first_seen=0.0,
    )
    assert [(c.utt_id, c.speaker.track_id, c.text) for c in caps] == [
        ("9", 1, "Did you hear the doorbell a minute ago")
    ]


# ---------------- V-18 stable captions across drafts ----------------
A = Speaker("face", 1, None, "Person in white shirt", "none")
B = Speaker("face", 2, None, "Person in blue shirt", "none")
SOMEONE = Speaker("someone", label="Someone", side="none")


def spoken(n, start=0.0, step=0.3, length=0.25):
    """n words, one every `step` seconds."""
    return [(f"w{i}", start + i * step, start + i * step + length) for i in range(n)]


def draft(utt, words, final=False):
    return {
        "utt_id": utt,
        "t_start": words[0][1],
        "t_end": words[-1][2],
        "text": " ".join(w[0] for w in words),
        "final": final,
        "lang": "en",
        "words": words,
    }


def stream(f, utt, words, timeline, every=0.56, latency=0.25):
    """Feed growing drafts (then the final) the way Section 2 streams them. The timeline
    only holds the decisions made by each draft's time. Returns [(captions, retracted)]."""
    out, audio_t = [], every
    while True:
        final = audio_t >= words[-1][2]
        now = audio_t + latency
        f.timeline.clear()
        f.timeline.extend((t, s) for t, s in timeline if t <= now)
        ws = words if final else [w for w in words if w[2] <= audio_t]
        if ws:
            caps = f._captions_for(draft(utt, ws, final), now, first_seen=0.0)
            out.append((caps or [], [r.utt_id for r in f.take_retractions()]))
        if final:
            return out
        audio_t += every


def speakers_per_id(sends):
    seen = {}
    for caps, _ in sends:
        for c in caps:
            seen.setdefault(c.utt_id, []).append((c.speaker.kind, c.speaker.track_id))
    return seen


def test_speaker_stays_put_across_drafts_with_a_jittery_timeline():
    # A talks for 4 s; the decision keeps flickering to B and to nobody for a moment.
    # Re-splitting every draft from scratch flipped the whole caption to B at 2 s.
    words = spoken(14)
    timeline = [
        (0.0, A),
        (0.6, B),
        (1.6, A),
        (1.9, SOMEONE),
        (2.1, A),
        (2.8, B),
        (3.0, A),
    ]
    sends = stream(SpeakerFusion(FusionSettings()), "u1", words, timeline)
    assert len(sends) >= 7
    for uid, who in speakers_per_id(sends).items():
        assert len(set(who)) == 1, (uid, who)  # one speaker per segment id, every draft
    assert [c.utt_id for c in sends[0][0]] == ["u1"]
    assert all(
        c.speaker.track_id == 1 for caps, _ in sends for c in caps if c.utt_id == "u1"
    )


def test_label_changes_for_the_same_face_do_not_split_a_caption():
    f = SpeakerFusion(FusionSettings())
    named = Speaker("face", 1, None, "Person in blue", "none")
    f.timeline.extend([(0.0, A), (1.0, named)])  # a new label arrived mid-sentence
    caps = f._captions_for(draft("u2", spoken(8), True), now=5.0, first_seen=0.0)
    assert [(c.utt_id, c.speaker.track_id) for c in caps] == [("u2", 1)]
    assert caps[0].speaker.label == "Person in blue"  # the newest label is shown


def test_a_segment_dropped_by_a_later_draft_is_retracted():
    f = SpeakerFusion(FusionSettings())
    words = spoken(12)  # 0.0 - 3.55 s
    f.timeline.extend([(0.0, A), (1.2, SOMEONE)])  # the face left at 1.2 s
    caps = f._captions_for(draft("u3", words), now=4.0, first_seen=0.0)
    assert [(c.utt_id, c.speaker.kind) for c in caps] == [
        ("u3", "face"),
        ("u3.1", "someone"),
    ]
    assert f.take_retractions() == []
    # the final: the recogniser dropped the last words, so the unknown piece is short
    # enough to join the face's segment again, and "u3.1" must leave the pages
    caps = f._captions_for(draft("u3", words[:6], True), now=4.6, first_seen=0.0)
    assert [(c.utt_id, c.speaker.track_id) for c in caps] == [("u3", 1)]
    assert [r.utt_id for r in f.take_retractions()] == ["u3.1"]
    assert "u3" not in f.utts  # finished: nothing left to track


def test_a_face_leaving_does_not_relabel_what_it_said():
    sim = Sim()
    loud = lambda: -30 + 20 * syllables(sim.t)
    for _ in range(30):  # both faces in view, quiet, for 1 s
        sim.step({1: still_face(500), 2: still_face()}, loud=-60)
    for _ in range(24):  # A talks for 0.8 s
        sim.step({1: talking_face(sim.t), 2: still_face()}, speech=True, loud=loud())
    t0 = sim.t
    words = [
        ("Wait", t0 - 0.5, t0 - 0.35),
        ("I", t0 - 0.3, t0 - 0.2),
        ("think", t0 - 0.15, t0),
    ]
    sim.f.on_transcript(draft("u4", words), sim.t)
    _, caps, _ = sim.step(
        {1: talking_face(sim.t), 2: still_face()}, speech=True, loud=loud()
    )
    assert [(c.utt_id, c.speaker.track_id) for c in caps] == [("u4", 1)]
    # the speaker walks out of the frame on the left and keeps talking for 2 s
    sim.f.on_track_lost({"track_id": 1, "t": sim.t, "side": "left"})
    t_left = sim.t
    sent = []

    def unseen_words():
        n = int((sim.t - t_left) / 0.3)
        return [
            (f"x{k}", t_left + 0.1 + 0.3 * k, t_left + 0.35 + 0.3 * k) for k in range(n)
        ]

    for i in range(64):
        _, caps, _ = sim.step({2: still_face()}, speech=True, loud=loud())
        sent += caps
        if i % 17 == 16:  # drafts keep arriving with the words said off-screen
            sim.f.on_transcript(draft("u4", words + unseen_words()), sim.t)
    sim.f.on_transcript(draft("u4", words + unseen_words(), final=True), sim.t)
    _, caps, _ = sim.step({2: still_face()}, speech=True, loud=loud())
    sent += caps
    mine = [c for c in sent if c.utt_id == "u4"]
    # never re-sent as Someone
    assert len(mine) >= 3 and all(c.speaker.track_id == 1 for c in mine)
    last = {c.utt_id: c for c in caps}
    # the words said on camera stay with the face (so do the first unseen ones, which
    # joined it while too short to stand alone, as before)
    assert last["u4"].text.startswith("Wait I think")
    unseen = [c for c in caps if c.utt_id != "u4"]
    assert unseen and all(c.speaker.kind in ("someone", "offscreen") for c in unseen)
    # and it holds only words said while unseen
    assert all(w[1] >= t_left for c in unseen for w in c.words)
    assert sim.f.take_retractions() == []


def test_a_genuine_turn_change_still_splits_across_drafts():
    words = spoken(18)  # 0.0 - 5.35 s
    # A until 2.7 s, then B answers; the decision lags the change by 0.3 s
    timeline = [
        (0.0, SOMEONE),
        (0.2, A),
        (1.1, SOMEONE),
        (1.2, A),
        (3.0, B),
        (4.1, A),
        (4.3, B),
    ]
    sends = stream(SpeakerFusion(FusionSettings()), "u5", words, timeline)
    for uid, who in speakers_per_id(sends).items():
        assert len(set(who)) == 1, (uid, who)
    final = sends[-1][0]
    assert [(c.utt_id, c.speaker.track_id) for c in final] == [("u5", 1), ("u5.1", 2)]
    assert final[1].words[0][1] >= 2.7  # the answer's first words went back to B
    assert all(not gone for _, gone in sends)


def test_first_words_wait_only_once_per_utterance():
    sim = Sim()
    sim.step({}, speech=True)
    words = [("Hello", sim.t, sim.t + 0.3)]
    sim.f.on_transcript(draft("u6", words), sim.t)
    _, caps, _ = sim.step({}, speech=True, dt=0.35)
    assert len(caps) == 1 and caps[0].speaker.kind == "someone"
    # the next draft still has nobody to show, but it is not held back again
    words = words + [("there", sim.t, sim.t + 0.2)]
    sim.f.on_transcript(draft("u6", words), sim.t)
    _, caps, _ = sim.step({}, speech=True)
    assert [c.text for c in caps] == ["Hello there"]


def test_a_someone_segment_takes_the_face_that_turns_up():
    f = SpeakerFusion(FusionSettings())
    words = spoken(6)
    f.timeline.append((0.0, SOMEONE))
    caps = f._captions_for(draft("u7", words[:3]), now=1.2, first_seen=0.0)
    assert caps[0].speaker.kind == "someone"
    f.timeline.append((0.1, A))  # a late decision covering those words
    caps = f._captions_for(draft("u7", words), now=2.0, first_seen=0.0)
    assert [(c.utt_id, c.speaker.track_id) for c in caps] == [("u7", 1)]


def test_a_stretched_last_word_does_not_make_its_own_segment():
    # the streaming recogniser stretches each draft's last word to the end of the chunk
    f = SpeakerFusion(FusionSettings())
    f.timeline.extend([(0.0, A), (0.62, B), (1.2, A)])  # a flicker under "starts at"
    drafts = [
        [("The", 0.0, 0.9)],
        [("The", 0.0, 0.3), ("meeting", 0.3, 0.6), ("starts", 0.62, 1.5)],
        [("The", 0.0, 0.3), ("meeting", 0.3, 0.6), ("starts", 0.62, 0.9)]
        + [("at", 0.95, 1.1), ("three", 1.15, 1.5)],
    ]
    for i, words in enumerate(drafts):
        caps = f._captions_for(draft("u8", words, i == 2), now=2.0 + i, first_seen=0.0)
        assert [(c.utt_id, c.speaker.track_id) for c in caps] == [("u8", 1)]
    assert f.take_retractions() == []


def test_shown_words_need_a_longer_run_to_move_to_another_speaker():
    f = SpeakerFusion(FusionSettings())
    words = [
        ("Did", 0.0, 0.3),
        ("you", 0.35, 0.5),
        ("hear", 0.55, 0.8),
        ("the", 0.85, 1.0),
    ]
    words += [("doorbell", 1.05, 1.4), ("a", 1.45, 1.55), ("minute", 1.6, 1.85)]
    words += [("ago", 1.9, 2.1)]
    f.timeline.extend([(0.0, A), (1.1, B)])  # a late flicker to B for the last second
    caps = f._captions_for(draft("u9", words[:5]), now=1.5, first_seen=0.0)
    assert [(c.utt_id, c.speaker.track_id) for c in caps] == [("u9", 1)]
    # 1.05 s of B, including "doorbell" already shown with A: not enough to move it
    caps = f._captions_for(draft("u9", words, True), now=2.5, first_seen=0.0)
    assert [(c.utt_id, c.speaker.track_id) for c in caps] == [("u9", 1)]
    assert f.take_retractions() == []
