"""V-31: two people taking turns each get their own bubbles.

Light-ASD hears a new talker about half a second after they start, so without these a
short reply ("Okay.", "Do you like the people?") was shown in the other person's bubble:
its few words with the new speaker's evidence were shorter than `min_segment_s` and were
merged away, and the second person never got a bubble of their own.
"""

from collections import deque

from attune.fusion.speaker import SpeakerFusion
from attune.vision.settings import FusionSettings
from attune.vision.types import Speaker

from .test_asd import Sim

A = Speaker("face", 1, None, "A")
B = Speaker("face", 2, None, "B")


def _words(text, t0, step=0.2, gap=0.02):
    out, t = [], t0
    for w in text.split():
        out.append((w, t, t + step - gap))
        t += step
    return out


def _captions(timeline, words, **settings):
    """Captions for one final transcript, given the decided speaker timeline."""
    f = SpeakerFusion(FusionSettings(**settings))
    f.timeline = deque(timeline)
    ev = {"utt_id": "u1", "t_start": words[0][1], "t_end": words[-1][2], "final": True,
          "text": " ".join(w[0] for w in words), "words": words}  # fmt: skip
    return [(c.speaker.track_id, c.text) for c in f._captions_for(ev, 10.0, 10.0)]


def test_a_short_reply_whose_evidence_came_late_gets_its_own_bubble():
    # A: 2 s | pause 0.3 s | B: "do you like the people" (1 s; Light-ASD hears B from 2.9 s)
    # | pause 0.3 s | A again (heard at once)
    words = _words("so what do you think about the new place", 0.2)
    words += _words("do you like the people", 2.3)
    words += _words("yes I do mostly", 3.6)
    caps = _captions([(0.0, A), (2.9, B), (3.6, A)], words)
    assert caps == [
        (1, "so what do you think about the new place"),
        (2, "do you like the people"),
        (1, "yes I do mostly"),
    ]


def test_a_one_word_reply_between_pauses_is_a_turn_of_its_own():
    words = _words("we should head out soon", 0.2) + [("Okay.", 1.5, 1.9)]
    words += _words("let me grab my jacket", 2.2)
    caps = _captions([(0.0, A), (1.5, B), (2.2, A)], words)
    assert caps == [(1, "we should head out soon"), (2, "Okay."), (1, "let me grab my jacket")]


def test_a_flicker_inside_a_sentence_still_merges_into_it():
    # no pause around it: a listener's Light-ASD flicker, not a reply
    words = _words("it was a really long drive but the view was worth it", 0.0)
    caps = _captions([(0.0, A), (1.0, B), (1.3, A)], words)
    assert caps == [(1, "it was a really long drive but the view was worth it")]


def test_a_switch_light_asd_made_is_dated_back_to_when_the_reply_began():
    sim = Sim()
    sim.run(2.0, {1: (300, 0.05, True, 2.5), 2: (900, 0.0, False, -3.0)})
    assert sim.f.current.track_id == 1
    for _ in range(60):
        sim.step({1: (300, 0.0, False, -3.0), 2: (900, 0.05, True, 2.5)})
        if sim.f.current.track_id == 2:
            break
    decided = sim.t
    when, spk = sim.f.timeline[-1]
    assert spk.track_id == 2
    assert abs((decided - when) - sim.f.s.asd_switch_lag_s) < 1e-6
    sim = Sim(asd_switch_lag_s=0.0)  # off
    sim.run(2.0, {1: (300, 0.05, True, 2.5), 2: (900, 0.0, False, -3.0)})
    sim.run(1.0, {1: (300, 0.0, False, -3.0), 2: (900, 0.05, True, 2.5)})
    assert sim.f.timeline[-1][0] > 2.0


def test_the_voice_of_a_still_face_does_not_veto_the_face_light_asd_hears():
    # face 2 has no Light-ASD score (too small, say) and its mouth is still; the voice match
    # still hears its last turn. Face 1, which Light-ASD hears talking, keeps the words.
    sim = Sim()
    faces = {1: (300, 0.05, True, 2.5), 2: (900, 0.0, False, None)}
    spk = sim.run(3.0, faces, voice="track-2")
    assert spk.kind == "face" and spk.track_id == 1
