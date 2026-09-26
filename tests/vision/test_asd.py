"""V-22: Light-ASD active speaker detection, with a stub model (no weights, no GPU).

Covers the features (MFCC, mouth crop), the audio ring, the scoring thread's windows,
the vision service glue, and the fusion hook: a face Light-ASD calls silent never
gets the words, a face it calls talking does even with still-looking lips, and
without scores everything falls back to the lip-score rules.
"""

import math
import time

import numpy as np
import pytest
from attune.fusion.speaker import SpeakerFusion
from attune.vision import types as T
from attune.vision.asd import FPS, SR, ActiveSpeakerDetector, AudioRing, asd_crop, mfcc
from attune.vision.detector import Detection
from attune.vision.service import VisionService
from attune.vision.settings import FusionSettings
from attune.vision.tracker import FaceTrack, KalmanBox
from attune.vision.types import Speaker, Track, Tracks

from .conftest import FakeBus


# ---------------------------------------------------------------- features
def _mfcc_reference(sig: np.ndarray) -> np.ndarray:
    """python_speech_features.mfcc(sig, 16000, numcep=13) written out frame by frame."""
    sig = np.append(sig[0], sig[1:] - 0.97 * sig[:-1]).astype(np.float64)
    n = 1 + math.ceil((len(sig) - 400) / 160)
    sig = np.concatenate([sig, np.zeros((n - 1) * 160 + 400 - len(sig))])
    mel = lambda hz: 2595 * np.log10(1 + hz / 700.0)
    hz = lambda m: 700 * (10 ** (m / 2595.0) - 1)
    bins = np.floor(513 * hz(np.linspace(mel(0), mel(8000), 28)) / 16000)
    out = []
    for f in range(n):
        frame = sig[f * 160 : f * 160 + 400]
        p = np.abs(np.fft.rfft(frame, 512)) ** 2 / 512
        fb = []
        for j in range(26):
            w = np.zeros(257)
            for i in range(int(bins[j]), int(bins[j + 1])):
                w[i] = (i - bins[j]) / (bins[j + 1] - bins[j])
            for i in range(int(bins[j + 1]), int(bins[j + 2])):
                w[i] = (bins[j + 2] - i) / (bins[j + 2] - bins[j + 1])
            fb.append(max(p @ w, np.finfo(float).eps))
        logfb = np.log(fb)
        c = []
        for k in range(13):
            scale = math.sqrt(1 / 26) if k == 0 else math.sqrt(2 / 26)
            c.append(
                scale
                * sum(
                    logfb[m] * math.cos(math.pi * k * (2 * m + 1) / 52)
                    for m in range(26)
                )
            )
        c = np.array(c) * (1 + 11 * np.sin(np.pi * np.arange(13) / 22))
        c[0] = np.log(max(p.sum(), np.finfo(float).eps))
        out.append(c)
    return np.array(out)


def test_mfcc_matches_the_frame_by_frame_reference():
    rng = np.random.default_rng(0)
    t = np.arange(4000) / SR
    sig = 3000 * np.sin(2 * np.pi * 220 * t) + 500 * rng.standard_normal(len(t))
    got, want = mfcc(sig), _mfcc_reference(sig)
    assert got.shape == want.shape == (1 + math.ceil((4000 - 400) / 160), 13)
    np.testing.assert_allclose(got, want, rtol=1e-6, atol=1e-6)


def test_mfcc_gives_exactly_four_frames_per_video_frame_for_a_window():
    frames = 25
    n = frames * SR // FPS + 240
    assert mfcc(np.zeros(n)).shape == (4 * frames, 13)
    assert np.allclose(mfcc(np.zeros(n))[:, 0], np.log(np.finfo(float).eps))  # silence


def test_crop_is_112_grey_centred_on_the_lower_face():
    image = np.zeros((720, 1280, 3), np.uint8)
    box = (600, 200, 700, 300)  # 100 px face: s = 50
    cx, cy = 650, 250 + 0.4 * 50  # the crop centre sits 0.4 s below the box centre
    image[int(cy) - 2 : int(cy) + 3, cx - 2 : cx + 3] = 255
    crop = asd_crop(image, box)
    assert crop.shape == (112, 112) and crop.dtype == np.uint8
    ys, xs = np.nonzero(crop > 100)
    assert abs(ys.mean() - 56) < 3 and abs(xs.mean() - 56) < 3
    # side is (1 + 0.4) * s = 70 px: a 5 px dot scales by 112 / 70
    assert 6 <= len(set(xs)) <= 10


def test_crop_pads_outside_the_frame_with_grey_110():
    image = np.full((720, 1280, 3), 20, np.uint8)
    crop = asd_crop(image, (1200, 650, 1300, 750))  # hangs off the right and bottom
    assert crop.shape == (112, 112)
    assert crop[-1, -1] == 110 and crop[0, 0] == 20
    assert asd_crop(image, (2000, 2000, 2100, 2100)) is None


# ---------------------------------------------------------------- audio ring
def test_audio_ring_returns_samples_by_time_and_restarts_on_a_jump():
    ring = AudioRing(keep_s=2.0)
    for i in range(100):  # 1 s of 10 ms blocks whose value is the block number
        ring.add(10.0 + i / 100, np.full(160, i, np.float32))
    got = ring.get(10.25, 320)
    assert got is not None and got[0] == 25 and got[-1] == 26
    assert ring.get(10.95, 1600) is None  # runs past the newest sample
    assert ring.get(9.0, 160) is None  # before the oldest
    ring.add(50.0, np.ones(160, np.float32))  # a jump: start over
    assert ring.get(10.25, 10) is None and ring.get(50.0, 160) is not None


# ---------------------------------------------------------------- scoring thread
class StubModel:
    """Scores each frame with the crop's mean brightness / 10 (so rows map back to faces)."""

    def __init__(self, delay_s: float = 0.0):
        self.calls = []
        self.delay_s = delay_s

    def score(self, audio, video):
        self.calls.append((audio.shape, video.shape))
        if self.delay_s:
            time.sleep(self.delay_s)
        return video.reshape(video.shape[0], video.shape[1], -1).mean(axis=2) / 10.0


def _feed(det, seconds, faces, t0=0.0, fps=30, gap=None):
    """Feed `seconds` of audio and 30 fps crops; faces: {track_id: brightness}."""
    for i in range(int(seconds * 100)):
        det.add_audio(t0 + i / 100, np.zeros(160, np.float32) + 0.01)
    for i in range(int(seconds * fps)):
        t = t0 + i / fps
        if gap and gap[0] <= t < gap[1]:
            continue
        for tid, level in faces.items():
            det.add_face(tid, t, np.full((112, 112), level, np.uint8))


def test_detector_scores_every_face_in_one_batch_with_aligned_shapes():
    model = StubModel()
    det = ActiveSpeakerDetector(model, window_s=1.0, score_s=0.2)
    _feed(det, 2.0, {1: 30, 2: 70})
    assert det.step() == 2
    (a_shape, v_shape) = model.calls[0]
    assert v_shape == (2, 25, 112, 112) and a_shape == (2, 100, 13)
    assert det.score(1, 2.0) == pytest.approx(3.0) and det.score(
        2, 2.0
    ) == pytest.approx(7.0)
    assert det.step() == 0  # nothing new to score


def test_detector_waits_for_history_and_audio_and_goes_stale():
    det = ActiveSpeakerDetector(StubModel(), window_s=1.0, max_age_s=0.5)
    _feed(det, 0.6, {1: 50})
    assert det.step() == 0  # under a second of frames
    det2 = ActiveSpeakerDetector(StubModel(), window_s=1.0, max_age_s=0.5)
    for i in range(60):
        det2.add_face(1, i / 30, np.full((112, 112), 50, np.uint8))
    assert det2.step() == 0  # no audio at all
    _feed(det, 2.0, {1: 50}, t0=0.6)
    assert det.step() == 1
    t_end = det.faces[1].score_t
    assert det.score(1, t_end + 0.4) is not None
    assert (
        det.score(1, t_end + 0.6) is None
    )  # stale: fusion falls back to the lip score


def test_detector_skips_windows_with_a_hole_in_the_frames():
    det = ActiveSpeakerDetector(StubModel(), window_s=1.0, max_gap_s=0.2)
    _feed(
        det, 2.0, {1: 50}, gap=(1.4, 1.65)
    )  # a 0.28 s hole (0.25 s of missing frames)
    assert det.step() == 0
    _feed(det, 1.2, {1: 50}, t0=2.0)
    assert det.step() == 1
    ok = ActiveSpeakerDetector(StubModel(), window_s=1.0, max_gap_s=0.2)
    _feed(ok, 2.0, {1: 50}, gap=(1.4, 1.5))  # a 0.13 s hole is fine
    assert ok.step() == 1


def test_detector_needs_a_fast_enough_vision_loop():
    slow = ActiveSpeakerDetector(StubModel(), window_s=1.0, min_fps=12)
    _feed(slow, 2.0, {1: 50}, fps=8)  # holes of 0.125 s pass, but 8 fps is too few
    assert slow.step() == 0
    fast = ActiveSpeakerDetector(StubModel(), window_s=1.0, min_fps=12)
    _feed(fast, 2.0, {1: 50}, fps=15)
    assert fast.step() == 1


def test_detector_forgets_faces_and_runs_on_its_own_thread():
    det = ActiveSpeakerDetector(StubModel(delay_s=0.01), rate_hz=20, window_s=1.0)
    _feed(det, 1.5, {1: 20, 2: 40})
    det.keep_only({2})
    assert set(det.faces) == {2}
    det.start()
    try:
        deadline = time.time() + 2.0
        while det.score(2, 1.5) is None and time.time() < deadline:
            time.sleep(0.02)
    finally:
        det.stop()
    assert det.score(2, 1.5) == pytest.approx(4.0)
    m = det.metrics()
    assert m["asd_ms"] is not None and m["asd_batch"] == 1 and m["asd_gpu_mb"] is None


def test_a_failing_model_is_reported_and_does_not_kill_the_thread():
    class Broken:
        def score(self, audio, video):
            raise RuntimeError("boom")

    det = ActiveSpeakerDetector(Broken(), rate_hz=50)
    _feed(det, 1.5, {1: 20})
    det.start()
    time.sleep(0.2)
    alive = det._thread.is_alive()
    det.stop()
    assert det.failed and alive


# ---------------------------------------------------------------- vision service glue
def test_service_without_weights_or_disabled_has_no_asd(tmp_path):
    svc = VisionService(
        FakeBus(), {"vision": {"asd_model": str(tmp_path / "none.model")}}
    )
    assert svc._load_asd() is None
    svc = VisionService(FakeBus(), {"vision": {"asd_enabled": False}})
    assert svc._load_asd() is None


def test_service_feeds_crops_and_16k_audio_and_publishes_the_score():
    bus = FakeBus()
    svc = VisionService(bus, {"vision": {"asd_min_face_px": 40}})
    svc.asd = ActiveSpeakerDetector(StubModel(), window_s=1.0)
    svc.connect()
    image = np.full((720, 1280, 3), 90, np.uint8)
    box = np.array([600.0, 200.0, 700.0, 300.0])
    small = np.array([100.0, 100.0, 130.0, 130.0])
    big = FaceTrack(
        7, KalmanBox(box), Detection(box, 0.9, np.zeros((5, 2))), 0.0, 0.0, 0.0
    )
    tiny = FaceTrack(
        8, KalmanBox(small), Detection(small, 0.9, np.zeros((5, 2))), 0.0, 0.0, 0.0
    )
    for i in range(60):
        t = i / 30
        bus.publish(
            T.AUDIO_BLOCK, {"t": t, "sample_rate": 16000, "samples": np.zeros(533)}
        )
        bus.publish(
            T.AUDIO_BLOCK, {"t": t, "sample_rate": 32000, "samples": np.ones(1066)}
        )
        svc._asd_crops(image, [big, tiny], t)
    assert set(svc.asd.faces) == {7}  # the 30 px face is too small
    assert svc.asd.audio.buf.max() == 0.0  # only the 16 kHz stream went in
    assert svc.asd.step() == 1
    assert svc._asd_score(7, 2.0) == pytest.approx(9.0)
    assert svc._asd_score(8, 2.0) is None
    svc._asd_crops(image, [tiny], 2.0)
    assert 7 not in svc.asd.faces  # gone from the tracker: forgotten


# ---------------------------------------------------------------- fusion hook
FPS_F = 30


def syllables(t, rate=4.0):
    return 0.5 + 0.5 * math.sin(2 * math.pi * rate * t)


class Sim:
    """Drives SpeakerFusion at 30 fps with faces that may carry an asd_score."""

    def __init__(self, **settings):
        self.f = SpeakerFusion(FusionSettings(**settings))
        self.t, self.frame = 0.0, 0

    def step(self, faces, speech=True, voice=None):
        """faces: {track_id: (x, lip_score, mouth_moving, asd_score or None)}"""
        self.t += 1 / FPS_F
        self.frame += 1
        tracks = []
        for tid, (x, lip, moving, asd) in faces.items():
            mouth = 0.2 + 0.15 * syllables(self.t) if moving else 0.1
            tracks.append(
                Track(
                    tid,
                    [x, 400, 120, 120],
                    120,
                    lip,
                    None,
                    None,
                    0.0,
                    "unknown",
                    mouth,
                    asd,
                )
            )
        self.f.on_tracks(Tracks(self.frame, self.t, tracks))
        self.f.on_vad({"t": self.t, "is_speech": speech, "prob": 0.9})
        self.f.on_audio_level({"t": self.t, "db": -30 + 20 * syllables(self.t)})
        if voice is not None:
            self.f.on_voice_match({"utt_id": "u1", "person_id": voice, "score": 0.8})
        return self.f.tick(self.t)

    def run(self, seconds, faces, **kw):
        for _ in range(int(seconds * FPS_F)):
            self.step(faces, **kw)
        return self.f.current


def test_heuristic_alone_credits_a_moving_mouth_that_asd_calls_silent():
    # the live problem: lips move (and happen to follow the loudness) while someone
    # off camera talks. Without Light-ASD the face gets the words...
    sim = Sim()
    assert sim.run(2.0, {1: (500, 0.05, True, None)}).kind == "face"
    # ...with Light-ASD saying "not talking", they go to Someone
    sim = Sim()
    spk = sim.run(2.0, {1: (500, 0.05, True, -3.0)})
    assert spk.kind == "someone"
    assert not any(f.is_speaker for f in sim.f.scene().faces)


def test_asd_talking_face_is_the_speaker_even_with_still_lips():
    sim = Sim()  # backlit: the landmarker barely sees the mouth move
    spk = sim.run(1.0, {1: (500, 0.0, False, 2.5)})
    assert spk.kind == "face" and spk.track_id == 1


def test_asd_hysteresis_on_at_asd_on_off_below_asd_off():
    sim = Sim(asd_on=0.0, asd_off=-1.0, hold_s=0.2)
    assert sim.run(0.5, {1: (500, 0.0, False, 0.5)}).kind == "face"
    assert (
        sim.run(0.5, {1: (500, 0.0, False, -0.5)}).kind == "face"
    )  # between: stays on
    assert sim.run(0.5, {1: (500, 0.0, False, -1.5)}).kind == "someone"
    assert (
        sim.run(0.5, {1: (500, 0.0, False, -0.5)}).kind == "someone"
    )  # needs asd_on again


def test_asd_holds_the_current_speaker_until_another_clearly_beats_it():
    sim = Sim(asd_switch_margin=1.0, hold_s=0.5)
    faces = {1: (300, 0.0, False, 2.0), 2: (900, 0.0, False, -3.0)}
    assert sim.run(1.0, faces).track_id == 1
    faces = {1: (300, 0.0, False, 2.0), 2: (900, 0.0, False, 2.5)}
    assert sim.run(1.0, faces).track_id == 1  # only 0.5 better
    faces = {1: (300, 0.0, False, 2.0), 2: (900, 0.0, False, 3.5)}
    assert sim.run(1.0, faces).track_id == 2


def test_stale_or_missing_scores_fall_back_to_the_lip_score():
    sim = Sim(asd_fresh_s=0.6)
    sim.run(1.0, {1: (500, 0.05, True, -3.0)})
    assert sim.f.current.kind == "someone"
    # the scores stop coming (model too slow, face too small): lip rules again
    assert sim.run(1.5, {1: (500, 0.05, True, None)}).kind == "face"


def test_uncovered_faces_keep_the_lip_rules_next_to_covered_ones():
    sim = Sim()
    faces = {1: (300, 0.05, True, -3.0), 2: (900, 0.05, True, None)}
    spk = sim.run(2.0, faces)
    assert spk.kind == "face" and spk.track_id == 2
    assert [f.label for f in sim.f.scene().faces] == ["Person", "Person"]


def test_asd_silence_sends_a_known_voice_offscreen():
    sim = Sim()
    sim.f.voice_labels["p-ana"] = "Ana"
    spk = sim.run(2.0, {1: (500, 0.05, True, -3.0)}, voice="p-ana")
    assert spk.kind == "offscreen" and spk.label == "Ana"


def test_you_still_wins_over_an_asd_face():
    sim = Sim(you_level_db=40.0)
    for _ in range(30):
        sim.f.on_sensor_levels(
            {"t": sim.t, "left": 400, "right": 400, "motor_on": False}
        )
        sim.step({1: (500, 0.0, False, 3.0)})
    assert sim.f.current.kind == "you"


def test_asd_use_false_ignores_the_scores():
    sim = Sim(asd_use=False)
    assert sim.run(2.0, {1: (500, 0.05, True, -3.0)}).kind == "face"


def test_background_words_go_to_someone_and_the_talkers_words_to_the_face():
    sim = Sim()
    face = {1: (500, 0.05, True, -3.0)}
    sim.run(1.5, face)  # someone off camera talks; the face's lips wobble
    start = sim.t
    sim.run(1.0, face)
    sim.f.on_transcript(
        {
            "utt_id": "u9",
            "t_start": start,
            "t_end": sim.t,
            "text": "over there",
            "final": True,
            "lang": "en",
            "words": [
                ("over", start + 0.1, start + 0.4),
                ("there", start + 0.5, start + 0.8),
            ],
        },
        sim.t,
    )
    caps = []
    for _ in range(15):  # "Someone" words wait first_words_wait_ms for a speaker first
        caps += sim.step(face)[1]
    assert caps and all(c.speaker.kind == "someone" for c in caps)

    face = {1: (500, 0.0, False, 2.5)}  # now the face itself talks
    sim.run(1.0, face)
    start = sim.t
    sim.run(1.0, face)
    sim.f.on_transcript(
        {
            "utt_id": "u10",
            "t_start": start,
            "t_end": sim.t,
            "text": "hello you",
            "final": True,
            "lang": "en",
            "words": [
                ("hello", start + 0.1, start + 0.4),
                ("you", start + 0.5, start + 0.8),
            ],
        },
        sim.t,
    )
    caps = sim.step(face)[1]  # a known speaker: shown at once
    assert caps and all(
        c.speaker.kind == "face" and c.speaker.track_id == 1 for c in caps
    )


def _eval_script():
    import importlib.util
    import os
    import sys

    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "eval_talker.py"
    )
    spec = importlib.util.spec_from_file_location("eval_talker", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look their module up while it loads
    spec.loader.exec_module(mod)
    return mod


def test_eval_script_scores_words_against_the_truth(tmp_path):
    ev = _eval_script()
    truth_file = tmp_path / "truth.json"
    truth_file.write_text(
        '{"people": {"ana": {"region": [0, 0, 0.5, 1]}, "bo": {"region": [0.5, 0, 1, 1]}},'
        ' "intervals": [{"t0": 0, "t1": 4, "who": "room"},'
        ' {"t0": 4, "t1": 8, "who": "ana"},'
        ' {"t0": 8, "t1": 10, "who": "bo", "tag": "mouth hidden"}]}'
    )
    truth = ev.Truth.load(str(truth_file))
    t0 = 100.0  # the engine-clock time the clip started
    faces = [
        {"track_id": 1, "box": [100, 200, 120, 120], "is_speaker": False},
        {"track_id": 2, "box": [900, 200, 120, 120], "is_speaker": False},
    ]

    def cap(uid, kind, tid, words):
        speaker = {"kind": kind, "track_id": tid}
        return {"type": "caption", "utt_id": uid, "speaker": speaker, "words": words}

    msgs = [
        (t0 + k * 0.1, {"type": "scene", "t": t0 + k * 0.1, "faces": faces})
        for k in range(100)
    ]
    msgs += [
        (
            t0 + 1.5,
            cap(
                "u1", "face", 1, [["a", t0 + 1.0, t0 + 1.2], ["b", t0 + 1.3, t0 + 1.4]]
            ),
        ),
        (
            t0 + 2.6,
            cap(
                "u2",
                "someone",
                None,
                [["c", t0 + 2.0, t0 + 2.2], ["d", t0 + 2.3, t0 + 2.5]],
            ),
        ),
        (t0 + 5.4, cap("u3", "face", 1, [["e", t0 + 5.0, t0 + 5.2]])),
        (t0 + 5.5, cap("u3.1", "face", 2, [["f", t0 + 5.3, t0 + 5.4]])),
        (t0 + 5.6, {"type": "caption_retract", "utt_id": "u3.1"}),
        (t0 + 9.5, cap("u4", "face", 2, [["g", t0 + 9.0, t0 + 9.2]])),
    ]
    run = {"t0": t0, "frame_size": [1280, 720], "messages": msgs}
    m = ev.metrics(run, truth, "test")
    assert m["background_words"] == 4 and m["background_on_face"] == 0.5
    assert m["own_face"]["ana"] == (1.0, 1)  # the retracted segment doesn't count
    assert m["own_face"]["bo [mouth hidden]"] == (1.0, 1)
    assert m["first_caption_latency_s"] == 0.5


def test_a_confident_asd_face_can_harvest_a_voice_print():
    sim = Sim(asd_harvest=1.5, harvest_after_s=0.5)
    harvests = []
    for _ in range(60):
        sim.t += 1 / FPS_F
        sim.frame += 1
        sim.f.on_tracks(
            Tracks(
                sim.frame,
                sim.t,
                [
                    Track(
                        1,
                        [500, 400, 120, 120],
                        120,
                        0.0,
                        "p-1",
                        "Bo",
                        0.9,
                        "named",
                        0.1,
                        3.0,
                    )
                ],
            )
        )
        sim.f.on_vad({"t": sim.t, "is_speech": True, "prob": 0.9})
        _, _, h = sim.f.tick(sim.t)
        if h is not None:
            harvests.append(h)
    assert harvests and harvests[0].person_id == "p-1"
    assert sim.f.current == Speaker("face", 1, "p-1", "Bo")
