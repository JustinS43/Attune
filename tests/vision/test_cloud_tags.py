"""V-32: cloud captions' speaker tags in who's talking, with scripted conversations.

A conversation is a list of turns (who talks when). The driver plays it at 30 fps into
SpeakerFusion: faces carry a Light-ASD score that follows the truth `asd_lag` late (the
window lags, so the local decision sticks to the last talker), the local recogniser sends
drafts and a final per utterance, and the fake cloud sends Google-like final results at
pauses with every word of its stream tagged. No network, no models.
"""

from dataclasses import dataclass

import pytest
from attune.fusion.cloud_tags import CloudTags, CloudTagSettings
from attune.fusion.service import FusionService
from attune.fusion.speaker import SpeakerFusion, _Group
from attune.vision import types as T
from attune.vision.settings import FusionSettings
from attune.vision.types import Track, Tracks

FPS = 30
X = {1: 300, 2: 900, 3: 600}  # where each face sits


@dataclass
class Turn:
    who: object  # a track id, or a name for a voice with no face in view
    t0: float
    t1: float


class Conv:
    """Plays a conversation into SpeakerFusion; `cloud=False` is today's local-only fusion."""

    def __init__(self, turns, faces=(1, 2), cloud=True, asd_lag=0.6, state="on", **settings):
        self.turns = turns
        self.faces = list(faces)
        self.asd_lag = asd_lag
        self.cloud = CloudTags(CloudTagSettings()) if cloud else None
        self.f = SpeakerFusion(FusionSettings(**settings), self.cloud)
        if cloud:
            self.f.on_cloud_state({"enabled": True, "state": state})
        self.t, self.frame = 0.0, 0
        self.events: list[tuple[float, object]] = []
        self.captions: list = []
        self.retracted: list[str] = []
        self.gone: dict[int, tuple[float, str]] = {}
        # every word, unique text, with who said it
        self.words = []
        for k, turn in enumerate(turns):
            t, i = turn.t0, 0
            while t + 0.25 <= turn.t1 + 1e-9:
                self.words.append((f"{turn.who}.{k}.{i}", t, t + 0.25, turn.who))
                t, i = t + 0.3, i + 1
        self.words.sort(key=lambda w: w[1])
        self.truth = {w[0]: w[3] for w in self.words}

    # ---- script
    def at(self, t: float, fn) -> None:
        self.events.append((t, fn))

    def utterance(self, uid: str, t0: float, t1: float, endpoint_s: float = 0.4) -> None:
        """The local recogniser: a draft every 0.5 s, the final `endpoint_s` after t1."""
        mine = [w[:3] for w in self.words if t0 <= w[1] and w[2] <= t1 + 1e-9]

        def ev(now, final):
            ws = [w for w in mine if w[2] <= now] if not final else mine
            if not ws:
                return
            text = " ".join(w[0] for w in ws)
            self.f.on_transcript(
                {
                    "utt_id": uid,
                    "t_start": t0,
                    "t_end": ws[-1][2],
                    "text": text,
                    "final": final,
                    "lang": "en",
                    "words": ws,
                },
                now,
            )

        d = t0 + 0.5
        while d < t1 + endpoint_s - 1e-9:
            self.at(d, lambda now: ev(now, False))
            d += 0.5
        self.at(t1 + endpoint_s, lambda now: ev(now, True))

    def cloud_final(self, at: float, stream: str, upto: float, tags: dict, since: float = 0.0):
        """A Google final result: every word of the stream up to `upto`, tagged."""

        def ev(now):
            ws = [
                (w[0], w[1], w[2], tags[w[3]])
                for w in self.words
                if since - 1e-9 <= w[1] and w[2] <= upto + 1e-9 and w[3] in tags
            ]
            self.f.on_cloud_words(
                {"stream_id": stream, "words": ws, "final": True, "t_end": upto}, now
            )

        self.at(at, ev)

    def leave(self, tid: int, t: float, side: str) -> None:
        self.gone[tid] = (t, side)

    # ---- playback
    def talking(self, who, t: float) -> bool:
        return any(u.who == who and u.t0 <= t <= u.t1 for u in self.turns)

    def run(self, until: float) -> "Conv":
        self.events.sort(key=lambda e: e[0])
        while self.t < until:
            self.t += 1 / FPS
            self.frame += 1
            tracks = []
            for tid in self.faces:
                if tid in self.gone and self.t >= self.gone[tid][0]:
                    continue
                asd = 2.0 if self.talking(tid, self.t - self.asd_lag) else -3.0
                box = [X[tid], 400, 120, 120]
                tracks.append(Track(tid, box, 120, 0.02, None, None, 0.9, "unknown", 0.2, asd))
            for tid, (t_gone, side) in self.gone.items():
                if t_gone <= self.t < t_gone + 1 / FPS:
                    self.f.on_track_lost({"track_id": tid, "side": side, "t": self.t})
            self.f.on_tracks(Tracks(self.frame, self.t, tracks))
            speech = any(u.t0 <= self.t <= u.t1 + 0.2 for u in self.turns)
            self.f.on_vad({"t": self.t, "is_speech": speech, "prob": 0.9})
            while self.events and self.events[0][0] <= self.t:
                self.events.pop(0)[1](self.t)
            _, caps, _ = self.f.tick(self.t)
            self.captions += [(self.t, c) for c in caps]
            self.retracted += [r.utt_id for r in self.f.take_retractions()]
        return self

    # ---- reading the result
    def finals(self) -> list:
        return [c for _, c in self.captions if c.final]

    def owner_of_words(self) -> dict:
        """word -> the speaker of the final caption that holds it."""
        return {w[0]: c.speaker for c in self.finals() for w in c.words}

    def right(self, word: str, spk) -> bool:
        who = self.truth[word]
        if isinstance(who, int):
            return spk.kind in ("face", "probable_face") and spk.track_id == who
        return spk.kind in ("offscreen", "someone")

    def score(self) -> float:
        got = self.owner_of_words()
        return sum(self.right(w, s) for w, s in got.items()) / max(len(got), 1)


def two_turns():
    """A talks for 3 s, then B gives a short reply without a pause the recogniser splits at."""
    return [Turn(1, 0.0, 3.0), Turn(2, 3.1, 4.3)]


# ---------------------------------------------------------------- two alternating speakers
def test_a_quick_reply_gets_its_own_bubble_on_the_right_face():
    turns = two_turns()
    local = Conv(turns, cloud=False)
    local.utterance("u1", 0.0, 4.3)
    local.run(6.0)  # local only, Light-ASD's late window can leave the reply in A's bubble

    conv = Conv(turns)
    conv.utterance("u1", 0.0, 4.3)
    conv.cloud_final(3.6, "s1", 3.0, {1: "1", 2: "2"})  # at the pause after A
    conv.cloud_final(5.0, "s1", 4.3, {1: "1", 2: "2"})  # after the reply
    conv.run(6.5)
    got = conv.owner_of_words()
    assert len(got) == len(conv.words)
    assert all(conv.right(w, s) for w, s in got.items())
    finals = conv.finals()
    assert [c.speaker.track_id for c in finals] == [1, 2]  # two bubbles, one per face
    assert finals[0].utt_id == "u1"
    assert conv.score() >= local.score()


def test_turns_back_and_forth_keep_their_faces():
    turns = [Turn(1, 0.0, 2.5), Turn(2, 2.6, 4.0), Turn(1, 4.1, 5.5), Turn(2, 5.6, 7.5)]
    conv = Conv(turns)
    conv.utterance("u1", 0.0, 4.0)
    conv.utterance("u2", 4.1, 7.5)
    tags = {1: "1", 2: "2"}
    for t_pause in (2.5, 4.0, 5.5, 7.5):
        conv.cloud_final(t_pause + 0.7, "s1", t_pause, tags)
    conv.run(9.5)
    assert conv.score() == 1.0
    assert [c.speaker.track_id for c in conv.finals()] == [1, 2, 1, 2]


def test_a_reply_in_the_middle_of_a_shown_caption_is_cut_out_when_the_final_comes():
    # A, a quick "yeah" from B, A again: one utterance for the local recogniser. The drafts
    # carry no tags; the final's tags give B's words their own bubble.
    turns = [Turn(1, 0.0, 2.5), Turn(2, 2.6, 3.4), Turn(1, 3.5, 5.0)]
    conv = Conv(turns)
    conv.cloud_final(1.5, "s1", 1.0, {1: "1"})  # an earlier pause: A is bound already
    conv.utterance("u1", 0.0, 5.0)
    conv.cloud_final(5.6, "s1", 5.0, {1: "1", 2: "2"})
    conv.run(7.0)
    finals = conv.finals()
    assert [c.speaker.track_id for c in finals] == [1, 2, 1]
    assert conv.score() == 1.0
    assert finals[0].utt_id == "u1"


def test_a_shown_piece_is_cut_where_the_final_s_tags_hear_another_voice():
    # a draft showed A's, B's and A's words as one piece (locked: already shown)
    a_spk, b_spk = T.Speaker("face", 1, None, "A"), T.Speaker("face", 2, None, "B")
    words = [(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(10)]
    tags = _tags(*[(w, t0, t1, "2" if i in (4, 5) else "1") for i, (w, t0, t1) in enumerate(words)])
    evidence = [b_spk if i in (4, 5) else a_spk for i in range(10)]  # the cloud's evidence
    shown = _Group(a_spk, list(words), ["u1"], True, evidence)
    pieces = tags.protect([shown])
    assert [[w[0] for w in g.words] for g in pieces] == [
        [f"w{i}" for i in range(4)],
        ["w4", "w5"],
        [f"w{i}" for i in range(6, 10)],
    ]
    assert [g.speaker.track_id for g in pieces] == [1, 2, 1]
    assert pieces[0].prev == ["u1"] and pieces[1].prev == pieces[2].prev == []  # id stays first
    assert pieces[1].locked  # B's two words are a turn: smoothing leaves them


def _tags(*words):
    """CloudTags holding these cloud words (word, t0, t1, tag) of one stream."""
    tags = CloudTags()
    tags.on_state({"enabled": True, "state": "on"})
    tags.on_words({"stream_id": "s1", "words": list(words), "final": True, "t_end": 9.0}, 9.0)
    return tags


def test_overlapping_words_match_the_cloud_word_with_the_same_text():
    tags = _tags(("near", 1.0, 1.45, "1"), ("could", 1.1, 1.5, "2"))
    a, b = tags.speaker_of[("s1", "1")], tags.speaker_of[("s1", "2")]
    assert tags.cloud_speaker_at(1.2, "near") == a  # both cover 1.2 s: the text decides
    assert tags.cloud_speaker_at(1.2, "Could,") == b
    assert tags.cloud_speaker_at(1.2) == b  # no text: the newest start on a tie


def test_inside_someone_s_sentence_a_turn_needs_two_words():
    a_spk, b_spk = T.Speaker("face", 1, None, "A"), T.Speaker("face", 2, None, "B")
    words = [(f"w{i}", i * 0.5, i * 0.5 + 0.45) for i in range(8)]

    def groups(other):
        pieces = [(a_spk, words[:3]), (b_spk, words[3 : 3 + other]), (a_spk, words[3 + other :])]
        return [_Group(spk, list(ws), [], False, [spk] * len(ws)) for spk, ws in pieces]

    one = _tags(*[(w, t0, t1, "2" if i == 3 else "1") for i, (w, t0, t1) in enumerate(words)])
    assert not one.protect(groups(1))[1].locked  # one 0.45 s word tagged B: not a turn
    two = _tags(*[(w, t0, t1, "2" if i in (3, 4) else "1") for i, (w, t0, t1) in enumerate(words)])
    assert two.protect(groups(2))[1].locked  # two words: a real interjection keeps its bubble
    end = _tags(*[(w, t0, t1, "2" if i == 7 else "1") for i, (w, t0, t1) in enumerate(words)])
    last = [
        _Group(a_spk, words[:7], [], False, [a_spk] * 7),
        _Group(b_spk, words[7:], [], False, [b_spk]),
    ]
    assert end.protect(last)[1].locked  # a one-word reply at the end is a turn


def test_one_wrongly_tagged_word_does_not_split_a_bubble():
    conv = Conv([Turn(1, 0.0, 3.0)])
    conv.utterance("u1", 0.0, 3.0)
    wrong = conv.words[5][0]

    def tags(now):
        ws = [(w[0], w[1], w[2], "2" if w[0] == wrong else "1") for w in conv.words]
        conv.f.on_cloud_words({"stream_id": "s1", "words": ws, "final": True, "t_end": 3.0}, now)

    conv.at(3.5, tags)
    conv.run(5.0)
    finals = conv.finals()
    assert len(finals) == 1 and finals[0].speaker.track_id == 1


# ---------------------------------------------------------------- overlapping speech
def test_overlapping_speech_goes_word_by_word_to_the_right_faces():
    turns = [Turn(1, 0.0, 3.0), Turn(2, 2.0, 5.0)]
    conv = Conv(turns)
    conv.utterance("u1", 0.0, 5.0)
    conv.cloud_final(3.3, "s1", 2.0, {1: "1", 2: "2"})
    conv.cloud_final(5.6, "s1", 5.0, {1: "1", 2: "2"})
    conv.run(7.0)
    faces = {c.speaker.track_id for c in conv.finals()}
    assert faces == {1, 2}
    assert conv.score() >= 0.8  # single words inside the overlap may fold into a neighbour
    assert not any(c.speaker.kind == "someone" for c in conv.finals())


# ---------------------------------------------------------------- restarts
def test_tags_renumbered_after_a_restart_keep_their_faces():
    # stream s1 ends at 9 s; s2 starts at 8 s (the 1 s overlap it re-hears) and numbers
    # the voices the other way round
    turns = [
        Turn(1, 0.0, 3.0),
        Turn(2, 3.1, 6.0),
        Turn(1, 6.1, 7.5),
        Turn(2, 7.6, 10.0),
        Turn(1, 10.1, 12.5),
    ]
    conv = Conv(turns)
    for u, (a, b) in enumerate([(0.0, 3.0), (3.1, 6.0), (6.1, 7.5), (7.6, 10.0), (10.1, 12.5)]):
        conv.utterance(f"u{u}", a, b)
    old, new = {1: "1", 2: "2"}, {1: "2", 2: "1"}
    for t_pause in (3.0, 6.0, 7.5):
        conv.cloud_final(t_pause + 0.7, "s1", t_pause, old)
    conv.cloud_final(9.2, "s1", 9.0, old)  # the old stream's last result
    conv.cloud_final(10.7, "s2", 10.0, new, since=8.0)
    conv.cloud_final(13.2, "s2", 12.5, new, since=8.0)
    conv.run(14.0)
    assert conv.score() == 1.0
    assert [c.speaker.track_id for c in conv.finals()] == [1, 2, 1, 2, 1]
    # the new stream's "1" took B's cloud speaker over the overlap; its "2" found A again
    cloud = conv.cloud
    assert cloud.speaker_of[("s2", "1")] == cloud.speaker_of[("s1", "2")]
    assert cloud.speakers[cloud.speaker_of[("s2", "2")]].track == 1


def test_wrong_tags_in_the_overlap_do_not_hand_a_voice_to_someone_else():
    # s1: X says w0-w5, then Y says w6-w10. s2 re-hears w6-w10 (Y is its "1", with two
    # words wrongly tagged "2") and then X talks a long time as its "2".
    y_words = [(f"w{i}", i * 0.5, i * 0.5 + 0.45) for i in range(6, 11)]
    old = [(f"w{i}", i * 0.5, i * 0.5 + 0.45, "1") for i in range(6)]
    old += [(w, a, b, "2") for w, a, b in y_words]
    new = [(w, a, b, "2" if w in ("w9", "w10") else "1") for w, a, b in y_words]
    new += [(f"x{i}", 5.5 + i * 0.5, 5.95 + i * 0.5, "2") for i in range(12)]
    tags = CloudTags()
    tags.on_state({"enabled": True, "state": "on"})
    tags.on_words({"stream_id": "s1", "words": old, "final": True, "t_end": 5.5}, 6.0)
    tags.on_words({"stream_id": "s2", "words": new, "final": True, "t_end": 11.5}, 12.0)
    y = tags.speaker_of[("s1", "2")]
    assert tags.speaker_of[("s2", "1")] == y  # the stronger overlap wins, not the longer tag
    assert tags.speaker_of[("s2", "2")] not in (y, tags.speaker_of[("s1", "1")])


def test_a_voice_quiet_during_the_overlap_binds_again_from_the_faces():
    turns = [Turn(1, 0.0, 3.0), Turn(2, 3.1, 6.0), Turn(1, 6.1, 9.0), Turn(2, 9.1, 11.0)]
    conv = Conv(turns)
    for u, (a, b) in enumerate([(0.0, 3.0), (3.1, 6.0), (6.1, 9.0), (9.1, 11.0)]):
        conv.utterance(f"u{u}", a, b)
    for t_pause in (3.0, 6.0):
        conv.cloud_final(t_pause + 0.7, "s1", t_pause, {1: "1", 2: "2"})
    conv.cloud_final(9.6, "s2", 9.0, {1: "7", 2: "8"}, since=7.0)  # only A in the overlap
    conv.cloud_final(11.6, "s2", 11.0, {1: "7", 2: "8"}, since=7.0)
    conv.run(12.5)
    got = conv.owner_of_words()
    late = [w for w in got if conv.truth[w] == 2 and float(w.split(".")[1]) == 3]
    assert late and all(conv.right(w, got[w]) for w in late)


# ---------------------------------------------------------------- off screen
def test_a_voice_with_no_face_gets_its_own_bubble_in_the_dock():
    turns = [Turn(1, 0.0, 3.0), Turn("behind", 3.1, 5.0)]
    conv = Conv(turns, faces=(1,))
    conv.utterance("u1", 0.0, 5.0)
    conv.cloud_final(3.6, "s1", 3.0, {1: "1", "behind": "2"})
    conv.cloud_final(5.6, "s1", 5.0, {1: "1", "behind": "2"})
    conv.run(7.0)
    finals = conv.finals()
    assert [c.speaker.kind for c in finals] == ["face", "offscreen"]
    dock = finals[1].speaker
    assert dock.label == "Someone" and dock.person_id.startswith("session-cloud-")
    assert conv.score() == 1.0


def test_two_voices_off_screen_are_two_bubbles_not_one_someone():
    turns = [Turn(1, 0.0, 2.0), Turn("left", 2.1, 3.5), Turn("right", 3.6, 5.0)]
    conv = Conv(turns, faces=(1,))
    conv.utterance("u1", 0.0, 5.0)
    tags = {1: "1", "left": "2", "right": "3"}
    conv.cloud_final(2.6, "s1", 2.0, tags)
    conv.cloud_final(5.6, "s1", 5.0, tags)
    conv.run(7.0)
    finals = conv.finals()
    assert [c.speaker.kind for c in finals] == ["face", "offscreen", "offscreen"]
    assert finals[1].speaker.person_id != finals[2].speaker.person_id


def test_a_bound_face_that_walks_out_keeps_its_label_in_the_dock():
    turns = [Turn(1, 0.0, 3.0), Turn(2, 3.1, 5.0), Turn(1, 5.1, 7.0)]
    conv = Conv(turns)
    conv.utterance("u1", 0.0, 3.0)
    conv.utterance("u2", 3.1, 5.0)
    conv.utterance("u3", 5.1, 7.0)
    tags = {1: "1", 2: "2"}
    for t_pause in (3.0, 5.0, 7.0):
        conv.cloud_final(t_pause + 0.6, "s1", t_pause, tags)
    conv.leave(1, 5.0, "left")  # A walks out to the left and keeps talking
    conv.run(8.5)
    finals = conv.finals()
    assert [c.speaker.kind for c in finals] == ["face", "face", "offscreen"]
    assert finals[2].speaker.side == "left"
    assert finals[2].speaker.label == finals[0].speaker.label
    assert finals[2].speaker.person_id is None  # a stranger: session-only, no id


# ---------------------------------------------------------------- waiting, fallback, off
def test_a_final_waits_for_the_tags_at_most_final_wait_ms():
    turns = two_turns()
    conv = Conv(turns)
    conv.utterance("u1", 0.0, 4.3)
    conv.cloud_final(3.6, "s1", 3.0, {1: "1", 2: "2"})  # the cloud never hears the reply
    conv.run(7.0)
    t_final = min(t for t, c in conv.captions if c.final)
    assert 4.7 + 1.2 - 0.05 <= t_final <= 4.7 + 1.2 + 2 / FPS


@pytest.mark.parametrize("state", ["fallback", "connecting", "unavailable", "paused"])
def test_a_final_never_waits_unless_the_cloud_is_on(state):
    conv = Conv(two_turns(), state=state)
    conv.utterance("u1", 0.0, 4.3)
    conv.cloud_final(3.6, "s1", 3.0, {1: "1", 2: "2"})
    conv.run(6.0)
    t_final = min(t for t, c in conv.captions if c.final)
    assert t_final <= 4.7 + 1.5 / FPS


def test_a_final_is_never_changed_by_tags_that_come_later():
    conv = Conv(two_turns(), state="fallback")
    conv.utterance("u1", 0.0, 4.3)
    conv.cloud_final(3.6, "s1", 3.0, {1: "1", 2: "2"})
    conv.run(5.0)
    sent, retracted = list(conv.captions), list(conv.retracted)
    assert any(c.final for _, c in sent)
    conv.cloud_final(5.2, "s1", 4.3, {1: "1", 2: "2"})  # late tags, re-tagging everything
    conv.cloud_final(5.4, "s1", 4.3, {1: "2", 2: "1"})
    conv.run(7.0)
    assert conv.captions == sent and conv.retracted == retracted


def test_turning_cloud_captions_off_forgets_every_tag():
    conv = Conv(two_turns())
    conv.cloud_final(3.6, "s1", 3.0, {1: "1", 2: "2"})
    conv.run(4.0)
    assert conv.cloud.words and conv.cloud.speakers
    conv.f.on_cloud_state({"enabled": False, "state": "off"})
    assert not conv.cloud.words and not conv.cloud.speakers and not conv.cloud._talk_t


def test_forget_session_forgets_the_tags_and_bindings():
    conv = Conv(two_turns())
    conv.cloud_final(3.6, "s1", 3.0, {1: "1", 2: "2"})
    conv.run(4.0)
    conv.f.forget_session()
    assert not conv.cloud.words and not conv.cloud.speakers
    assert conv.cloud.state == "on"  # still on: the next tags are used


def test_off_by_default_changes_nothing():
    """With no tags (cloud captions off), fusion gives exactly today's captions."""
    turns = [Turn(1, 0.0, 2.5), Turn(2, 2.6, 4.0), Turn(1, 4.1, 5.5)]
    runs = []
    for cloud, state in ((False, "on"), (True, "off")):
        conv = Conv(turns, cloud=cloud, state=state)
        conv.utterance("u1", 0.0, 4.0)
        conv.utterance("u2", 4.1, 5.5)
        conv.run(7.0)
        runs.append([(round(t, 4), c) for t, c in conv.captions])
        if cloud:
            assert not conv.cloud._talk_t  # nothing is even noted while off
    assert runs[0] == runs[1]


def test_settings_come_from_the_cloud_table_and_ignore_the_rest():
    s = CloudTagSettings.from_config(
        {"bind_min_s": 1.5, "turn_min_s": "0.4", "models": {"en-US": "x"}, "memory_s": "bad"}
    )
    assert s.bind_min_s == 1.5 and s.turn_min_s == 0.4 and s.memory_s == 90.0
    assert CloudTagSettings.from_config(None) == CloudTagSettings()


# ---------------------------------------------------------------- the service
class _Bus:
    def __init__(self):
        self.subs = {}

    def subscribe(self, topic, cb):
        self.subs.setdefault(topic, []).append(cb)

    def publish(self, topic, ev):
        for cb in self.subs.get(topic, []):
            cb(ev)


def test_the_service_feeds_cloud_tags_and_state_to_fusion():
    bus = _Bus()
    svc = FusionService(bus, {"cloud": {"bind_min_s": 0.9}}, clock=lambda: 10.0)
    svc.start()
    try:
        assert svc.fusion.cloud.s.bind_min_s == 0.9
        bus.publish(T.CLOUD_STATE, {"enabled": True, "state": "on"})
        bus.publish(
            T.SPEAKER_CLOUD,
            {"stream_id": "s1", "words": [("hi", 9.0, 9.3, "1")], "final": True, "t_end": 9.3},
        )
        assert svc.fusion.cloud.state == "on"
        assert svc.fusion.cloud.cloud_speaker_at(9.1) is not None
        bus.publish(T.SESSION_FORGET, {})
        assert not svc.fusion.cloud.words
    finally:
        svc.stop()
