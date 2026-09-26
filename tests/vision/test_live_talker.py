"""V-19: live talker detection. A silent face in view must not be given someone else's speech.

The fixture `fixtures/live_silent_face.npz` is a real live recording reduced to numbers (no
image or audio): a Brio 101 on a table below one face, a ceiling light behind the head. The
face stays silent while people off camera talk from 3 s on; its lips part now and then and
it yawns at 19-26 s. It holds, per processed frame, the MediaPipe mouth-open ratio and the
tracker box, and the mic's 20 ms loudness (dBFS) and the VAD decisions.
"""

import math
from pathlib import Path

import numpy as np
from attune.fusion.speaker import SpeakerFusion
from attune.fusion.sync import FLOOR_DB, Envelope, in_time_score
from attune.vision.mouth import LipHistory, band_pass, mouth_in_frame
from attune.vision.settings import FusionSettings
from attune.vision.types import Track, Tracks

FIXTURE = Path(__file__).parent / "fixtures" / "live_silent_face.npz"
TICK = 1 / 15


def replay_fixture(settings: FusionSettings | None = None):
    """Run the fixture through LipHistory and SpeakerFusion; (fusion, [(t, speaker)])."""
    d = np.load(FIXTURE)
    f = SpeakerFusion(settings or FusionSettings())
    lips = LipHistory()
    events = [(float(t), 0, i) for i, t in enumerate(d["frame_t"])]
    events += [(float(t), 1, i) for i, t in enumerate(d["env_t"])]
    events += [(float(t), 2, i) for i, t in enumerate(d["vad"][:, 0])]
    events.sort()
    decisions, next_tick = [], 0.0
    for t, kind, i in events:
        while next_tick <= t:
            f.tick(next_tick)
            decisions.append((next_tick, f.current))
            next_tick += TICK
        if kind == 0:
            mouth = float(d["mouth_open"][i])
            mouth = None if math.isnan(mouth) else mouth
            if mouth is not None:
                lips.add(t, mouth)
            box = [float(v) for v in d["box"][i]]
            track = Track(
                1, box, round(box[2]), lips.score(t), None, None, 0.0, "unknown", mouth
            )
            f.on_tracks(Tracks(i, t, [track]))
        elif kind == 1:
            f.on_audio_level({"t": t, "db": float(d["env_db"][i])})
        else:
            f.on_vad({"t": t, "is_speech": bool(d["vad"][i, 1])})
    return f, decisions


def test_fixture_is_small_and_holds_no_media():
    assert FIXTURE.stat().st_size < 100_000
    d = np.load(FIXTURE)
    assert set(d.files) == {
        "frame_t",
        "mouth_open",
        "box",
        "env_t",
        "env_db",
        "vad",
        "note",
    }


def test_live_silent_face_scores_low_with_the_band_passed_lip_score():
    d = np.load(FIXTURE)
    old, new = [], []
    hist = LipHistory()
    for t, m in zip(d["frame_t"], d["mouth_open"]):
        if math.isnan(m):
            continue
        hist.add(float(t), float(m))
        if t < 3.0:
            continue
        new.append(hist.score(float(t)))
        recent = [r for tt, r in hist.samples if t - tt <= 1.0]
        old.append(float(np.std(recent)))
    old, new = np.array(old), np.array(new)
    line = FusionSettings().lip_talking
    # the old score put this silent face above the old talking line (0.03) a quarter of the
    # time; the band-passed one keeps it under the new line more often. What's left is real
    # movement (lips parting, the yawn), which the in-time and sustained checks reject.
    assert np.mean(old >= 0.03) > 0.2
    assert np.mean(new >= line) < np.mean(old >= 0.03)
    assert np.percentile(new, 25) < 0.25 * line


def test_live_silent_face_is_not_given_the_off_camera_speech():
    _, decisions = replay_fixture()
    heard = [(t, spk) for t, spk in decisions if spk is not None and t >= 3.0]
    on_face = [t for t, spk in heard if spk.kind in ("face", "probable_face")]
    assert len(heard) > 300  # people talk for most of it
    # Before V-19 this face had the speech 31% of the time. Now at most one stretch of
    # lip movement that lined up with the sound for a moment remains (about 1 s).
    assert len(on_face) / len(heard) < 0.06
    assert not [t for t in on_face if 19.0 <= t <= 26.5]  # the yawn is not speech


def test_live_silent_face_captions_go_to_someone():
    # words of the off-camera talkers where the recogniser found them in the live run
    words = [
        ("like", 2.8, 3.1),
        ("all", 3.2, 3.4),
        ("the", 3.4, 3.5),
        ("data", 3.5, 3.9),
    ]
    words += [
        ("I", 5.5, 5.6),
        ("read", 6.1, 6.4),
        ("the", 6.5, 6.6),
        ("data", 6.6, 7.0),
    ]
    words += [
        ("and", 7.2, 7.3),
        ("then", 7.4, 7.6),
        ("stop", 10.1, 10.3),
        ("work", 10.3, 10.6),
    ]
    f2, decisions = replay_fixture()
    now = decisions[-1][0]
    f2.on_transcript(
        {
            "utt_id": "bg",
            "t_start": 2.8,
            "t_end": 10.6,
            "text": " ".join(w[0] for w in words),
            "final": True,
            "lang": "en",
            "words": words,
        },
        now,
    )
    caps = []
    for k in range(1, 8):  # the first words may wait up to first_words_wait_ms
        caps += f2.tick(now + k * TICK)[1]
    assert caps and all(c.speaker.kind == "someone" for c in caps)


# ---------------- synthetic: jittery-silent vs talking ----------------
FPS = 30


def speech_loudness(t: float) -> float:
    """dB of speech-like sound: about 4.5 syllables a second, with a short breath every 2.5 s."""
    if 2.2 < (t % 2.5):
        return -65.0
    return -40 + 25 * max(0.0, math.sin(2 * math.pi * 4.5 * t)) ** 0.7


class Room:
    """One face and a mic. The face's mouth is either still (with landmark jitter and a lip
    parting now and then) or follows the sound it makes."""

    def __init__(self, settings=None, seed=0):
        self.f = SpeakerFusion(settings or FusionSettings())
        self.lips = LipHistory()
        self.rng = np.random.default_rng(seed)
        self.t = 0.0
        self.frame = 0

    def step(self, talking: bool, speech: bool, parting: bool = False, voice=None):
        self.t += 1 / FPS
        self.frame += 1
        t = self.t
        loud = speech_loudness(t) if speech else -65.0
        if talking:  # the mouth leads the sound by about 0.1 s
            mouth = 0.05 + 0.25 * max(0.0, (speech_loudness(t + 0.1) + 40) / 25)
        else:
            mouth = 0.012 + (0.18 if parting else 0.0)
        mouth += 0.006 * self.rng.standard_normal()  # landmark jitter
        self.lips.add(t, mouth)
        track = Track(1, [500, 300, 300, 300], 300, self.lips.score(t), None, None, 0.0,
                      "unknown", mouth)  # fmt: skip
        self.f.on_tracks(Tracks(self.frame, t, [track]))
        self.f.on_audio_level({"t": t, "db": loud})
        self.f.on_vad({"t": t, "is_speech": speech})
        if voice is not None:
            self.f.on_voice_match(
                {"utt_id": "u", "person_id": voice[0], "score": voice[1]}
            )
        if self.frame % 2 == 0:
            return self.f.tick(t)
        return None


def test_synthetic_silent_face_with_jitter_and_lip_partings_never_speaks():
    room = Room()
    for _ in range(60):  # quiet room
        room.step(talking=False, speech=False)
    on_face = 0
    for i in range(
        600
    ):  # 20 s of someone off camera talking; lips part for 0.5 s every 3 s
        room.step(talking=False, speech=True, parting=45 <= (i % 90) < 60)
        spk = room.f.current
        on_face += spk is not None and spk.kind in ("face", "probable_face")
    assert on_face == 0


def test_synthetic_talker_gets_the_speech_quickly():
    room = Room()
    for _ in range(75):  # quiet until 2.5 s, where a phrase starts
        room.step(talking=False, speech=False)
    first = None
    for i in range(150):  # the face starts talking after a quiet moment
        room.step(talking=True, speech=True)
        if (
            first is None
            and room.f.current is not None
            and room.f.current.kind == "face"
        ):
            first = (i + 1) / FPS
    assert first is not None and first <= 0.8
    assert room.f.current.kind == "face"


def test_synthetic_talker_after_off_camera_speech_without_a_pause():
    room = Room()
    for _ in range(60):
        room.step(talking=False, speech=False)
    for _ in range(90):  # someone off camera talks for 3 s ...
        room.step(talking=False, speech=True)
    assert room.f.current.kind == "someone"
    first = None
    for i in range(120):  # ... and the face answers straight away (no silence between)
        room.step(talking=True, speech=True)
        if first is None and room.f.current.kind == "face":
            first = (i + 1) / FPS
    # a face that starts in the middle of someone's speech has to keep it up for
    # talk_sustain_s (plus the in-time window catching up) before it gets the speech
    assert first is not None and first <= FusionSettings().talk_sustain_s + 0.8


# ---------------- voice evidence ----------------
def test_a_voice_matching_someone_else_vetoes_a_talking_face():
    room = Room()
    for _ in range(60):
        room.step(talking=False, speech=False)
    for _ in range(
        90
    ):  # lips in time, but the voice is an enrolled person's, not this face's
        room.step(talking=True, speech=True, voice=("p-alex", 0.8))
    assert room.f.current.kind != "face"


def test_a_face_with_a_session_print_is_vetoed_when_the_voice_matches_nobody():
    room = Room()
    room.f.harvested.add("track-1")  # this face's voice was learnt earlier
    for _ in range(60):
        room.step(talking=False, speech=False)
    for _ in range(90):
        room.step(talking=True, speech=True, voice=(None, 0.1))
    assert room.f.current.kind != "face"
    # the audio side's "not enough speech yet" (None, 0.0) is not a verdict
    room2 = Room()
    room2.f.harvested.add("track-1")
    for _ in range(60):
        room2.step(talking=False, speech=False)
    for _ in range(90):
        room2.step(talking=True, speech=True, voice=(None, 0.0))
    assert room2.f.current.kind == "face"


def test_the_face_own_voice_lets_small_lip_movement_count():
    settings = FusionSettings()
    for voice, expected in ((None, False), (("track-1", 0.8), True)):
        f = SpeakerFusion(settings)
        t = 0.0
        for frame in range(1, 121):
            t += 1 / FPS
            speech = frame > 30
            mouth = 0.02 + (0.01 * math.sin(2 * math.pi * 4.5 * t) if speech else 0.0)
            # lips in the probable band only, and no sound to check them against
            lip = 0.009 if speech else 0.001
            tr = Track(
                1, [500, 300, 300, 300], 300, lip, None, None, 0.0, "unknown", mouth
            )
            f.on_tracks(Tracks(frame, t, [tr]))
            f.on_vad({"t": t, "is_speech": speech})
            if voice is not None and speech:
                f.on_voice_match(
                    {"utt_id": "u", "person_id": voice[0], "score": voice[1]}
                )
            f.tick(t)
        assert (f.current is not None and f.current.kind == "face") is expected


# ---------------- clean voice learning ----------------
def test_a_silent_face_never_learns_the_off_camera_voice():
    room = Room()
    harvests = []
    for _ in range(60):
        room.step(talking=False, speech=False)
    for i in range(900):
        out = room.step(talking=False, speech=True, parting=30 <= (i % 60) < 50)
        if out is not None and out[2] is not None:
            harvests.append(out[2])
    assert harvests == [] and not room.f.harvested


def test_a_talking_face_learns_its_voice_after_harvest_after_s():
    room = Room()
    harvests = []
    for _ in range(60):
        room.step(talking=False, speech=False)
    for _ in range(150):
        out = room.step(talking=True, speech=True)
        if out is not None and out[2] is not None:
            harvests.append(out[2])
    assert harvests and harvests[0].person_id == "track-1"
    assert harvests[0].t1 - harvests[0].t0 >= FusionSettings().harvest_after_s - 1e-6
    assert "track-1" in room.f.harvested


def test_speech_given_by_voice_alone_is_not_harvested():
    f = SpeakerFusion(FusionSettings())
    f._decided_by = "voice"
    f.current = None
    # a face given the speech by its voice, with a perfect in-time score, still isn't
    # "talking on its own evidence", so nothing is learnt from it
    t, harvests = 0.0, []
    for frame in range(1, 91):
        t += 1 / FPS
        mouth = 0.02 + 0.004 * math.sin(2 * math.pi * 4.5 * t)
        tr = Track(
            1, [500, 300, 300, 300], 300, 0.009, None, None, 0.0, "unknown", mouth
        )
        f.on_tracks(Tracks(frame, t, [tr]))
        f.on_vad({"t": t, "is_speech": True})
        f.on_voice_match({"utt_id": "u", "person_id": "track-1", "score": 0.9})
        _, _, h = f.tick(t)
        harvests += [h] if h else []
    assert f.current.kind == "face" and harvests == []


# ---------------- building blocks ----------------
def test_band_pass_drops_slow_movement_and_keeps_syllables():
    t = np.arange(0, 3, 1 / 25)
    ramp = 0.3 * t / 3  # a mouth slowly opening
    step = np.where(t > 1.5, 0.2, 0.0)  # lips part and stay parted
    talk = 0.1 * np.sin(2 * np.pi * 4.5 * t)
    rms = lambda x: float(np.sqrt(np.mean(x[15:-15] ** 2)))
    assert rms(band_pass(ramp)) < 0.002
    assert rms(band_pass(talk)) > 0.03
    # a step only shows around its edge: nothing is left 0.3 s either side of it
    moved = np.abs(band_pass(step))
    assert moved[np.abs(t - 1.5) > 0.3].max() < 1e-9 and moved.max() > 0.05


def test_lip_score_needs_recent_measurements():
    h = LipHistory()
    for i in range(30):
        h.add(i / 30, 0.2 + 0.1 * math.sin(2 * math.pi * 4 * i / 30))
    assert h.score(1.0) > 0.03
    assert h.score(1.5) == 0.0  # the mouth hasn't been seen for 0.5 s


def test_mouth_outside_the_image_is_not_measured():
    pts = np.zeros((478, 2), np.float32)
    for k in (0, 13, 14, 17, 78, 308):
        pts[k] = (128, 200)  # crop pixels: low in the 256 px crop
    box = np.array([500.0, 500.0, 600.0, 600.0])  # crop spans y 470-630
    assert mouth_in_frame(pts, box, (720, 1280, 3))
    assert not mouth_in_frame(
        pts, box, (560, 1280, 3)
    )  # frame ends at y 560: mouth below


def test_digital_silence_does_not_dominate_the_in_time_check():
    env = Envelope()
    lips = []
    for i in range(75):
        t = i / FPS
        db = -40 + 20 * max(0.0, math.sin(2 * math.pi * 4 * t))
        if 1.0 <= t < 1.2:
            db = -180.0  # a dropped audio block
        env.add(t, db)
        lips.append((t, 0.2 + 0.1 * max(0.0, math.sin(2 * math.pi * 4 * (t + 0.05)))))
    assert min(db for _, db in env.samples) == FLOOR_DB
    assert in_time_score(lips, env, 2.5) > 0.5
