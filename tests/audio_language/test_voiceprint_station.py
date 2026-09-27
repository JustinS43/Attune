"""A-21: station voice prints (laptop mic) heard on the glasses mic, and bounded refinement.

The extractor is fake: a clip filled with the value k stands for voice k, so every score
below is set by the test.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from attune.audio.service import AudioService
from attune.audio.voiceprint import GLASSES, STATION, VoicePrints, write_print

ANGLE = {  # voice k -> a 2-D unit vector at this angle (degrees) from voice 0
    0: 0,
    1: 50,  # cos 0.64: the same person on the glasses mic
    2: 65,  # cos 0.42
    3: 80,  # cos 0.17: someone else
    4: 90,
    5: 180,  # nothing alike
}


def vec(k):
    a = np.radians(ANGLE[k])
    return np.array([np.cos(a), np.sin(a)], np.float32)


def extract(samples):
    return vec(round(float(samples[0])))


def clip(k, seconds=2.0):
    return np.full(int(seconds * 16000), float(k), np.float32)


OPTIONS = {
    "station_match": 0.4,
    "adapt_min": 0.4,
    "adapt_max_prints": 3,
    "adapt_min_s": 1.0,
    "adapt_gap_s": 0.0,
}


def prints(tmp_path, options=OPTIONS):
    return VoicePrints(tmp_path, extract, 0.5, 5, 1, options=options)


def test_station_scores_are_shifted_onto_the_voice_match_scale(tmp_path):
    write_print(tmp_path, "sam", vec(0), 1.0, STATION)
    write_print(tmp_path, "ana", vec(5), 1.0, GLASSES)
    v = prints(tmp_path)
    assert v.sources == {"sam": STATION, "ana": GLASSES}
    scores = v.scores(vec(2))
    assert scores["sam"] == pytest.approx(0.42 + 0.1, abs=0.01)  # + (0.5 - 0.4)
    assert scores["ana"] == pytest.approx(
        float(vec(2) @ vec(5))
    )  # glasses print: unshifted
    assert v.match(clip(2)) == ("sam", pytest.approx(0.52, abs=0.01))


def test_voice_lookup_uses_close_tier_then_checks_other_when_needed(tmp_path):
    write_print(tmp_path, "sam", vec(0), 1.0)
    write_print(tmp_path, "ana", vec(5), 1.0)
    (tmp_path / "sam" / "meta.json").write_text(json.dumps({"tier": "close"}))
    (tmp_path / "ana" / "meta.json").write_text(json.dumps({"tier": "other"}))
    voices = prints(tmp_path)
    checked = []
    score_tier = voices._score_tier

    def spy(tier, vector):
        checked.append(tier)
        return score_tier(tier, vector)

    voices._score_tier = spy
    assert voices.match(clip(0))[0] == "sam"
    assert checked == ["close"]
    checked.clear()
    assert voices.match(clip(5))[0] == "ana"
    assert checked == ["close", "other"]


def test_old_files_are_glasses_prints_and_the_bank_is_capped_on_load(tmp_path):
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "voice.json").write_text(
        json.dumps({"consent": True, "consent_t": 5.0, "embedding": [1.0, 0.0]})
    )
    write_print(tmp_path, "sam", vec(0), 1.0, STATION, [vec(1)] * 5)
    v = prints(tmp_path)
    assert v.sources["old"] == GLASSES and v.adapted["old"] == []
    assert len(v.adapted["sam"]) == 3  # adapt_max_prints


@pytest.mark.parametrize(
    ("voice", "talkers", "seconds", "why"),
    [
        (1, 2, 2.0, "several talkers"),
        (1, 1, 0.5, "short"),
        (3, 1, 2.0, "not like their print"),
    ],
)
def test_refinement_gates(tmp_path, voice, talkers, seconds, why):
    write_print(tmp_path, "sam", vec(0), 1.0, STATION)
    v = prints(tmp_path)
    assert v.harvest("sam", clip(voice, seconds), talkers) == why
    assert v.adapted["sam"] == []


def test_refinement_needs_the_person_to_score_best(tmp_path):
    write_print(tmp_path, "sam", vec(0), 1.0, STATION)
    write_print(tmp_path, "ana", vec(2), 1.0, GLASSES)
    v = prints(tmp_path)
    # voice 1 scores 0.64 + 0.1 for sam but 0.97 for ana: never learned as sam's
    assert v.harvest("sam", clip(1)) == "closer to someone else"


def test_refinement_is_rate_limited(tmp_path):
    write_print(tmp_path, "sam", vec(0), 1.0, STATION)
    v = prints(tmp_path, {**OPTIONS, "adapt_gap_s": 5.0})
    now = [100.0]
    v.clock = lambda: now[0]
    assert v.harvest("sam", clip(1)) == "adapted"
    now[0] += 2.0
    assert v.harvest("sam", clip(1)) == "too soon"
    now[0] += 4.0
    assert v.harvest("sam", clip(1)) == "adapted"


def test_refinement_adds_a_capped_bank_and_never_replaces_the_base_print(tmp_path):
    write_print(tmp_path, "sam", vec(0), 1.0, STATION)
    v = prints(tmp_path)
    before = v.scores(vec(1))["sam"]
    for _ in range(5):
        assert v.harvest("sam", clip(1)) == "adapted"
    assert len(v.adapted["sam"]) == 3
    np.testing.assert_allclose(v.enrolled["sam"], vec(0))
    after = v.scores(vec(1))["sam"]
    assert after == pytest.approx(1.0) and after > before  # the glasses-mic bank
    assert v.scores(vec(0))["sam"] == pytest.approx(1.1)  # the base print still counts
    saved = json.loads((tmp_path / "sam" / "voice.json").read_text())
    assert saved["source"] == STATION and saved["consent_t"] == 1.0
    np.testing.assert_allclose(saved["embedding"], vec(0), atol=1e-6)
    assert len(saved["adapted"]) == 3
    again = prints(tmp_path)  # the bank survives a restart...
    assert len(again.adapted["sam"]) == 3
    again.delete("sam")  # ...and goes with the person
    assert not (tmp_path / "sam").joinpath("voice.json").exists()
    assert "sam" not in again.adapted and "sam" not in again.enrolled


def test_the_bank_has_its_own_line_and_never_vouches_for_itself(tmp_path):
    write_print(tmp_path, "sam", vec(0), 1.0, STATION, [vec(2)])
    v = prints(tmp_path, {**OPTIONS, "bank_match": 0.6})
    assert v.scores(vec(2))["sam"] == pytest.approx(
        1.0 - 0.1
    )  # bank, shifted by 0.5 - 0.6
    # voice 3 is close to the bank (0.97) but not to the base print (0.17 + 0.1): not learned
    assert v.harvest("sam", clip(3)) == "not like their print"
    assert len(v.adapted["sam"]) == 1


def test_refinement_can_stay_in_memory(tmp_path):
    write_print(tmp_path, "sam", vec(0), 1.0, STATION)
    v = prints(tmp_path, {**OPTIONS, "adapt_persist": False})
    assert v.harvest("sam", clip(1)) == "adapted"
    assert json.loads((tmp_path / "sam" / "voice.json").read_text())["adapted"] == []


def test_strangers_still_get_session_prints_only(tmp_path):
    v = prints(tmp_path)
    assert v.harvest("track-3", clip(3)) == "session"
    assert "track-3" in v.session and not list(tmp_path.iterdir())
    v.forget()
    assert not v.session


def test_automatic_face_can_keep_harvested_voice_without_audio(tmp_path):
    v = prints(tmp_path)
    assert v.harvest("track-4", clip(0)) == "session"
    assert v.remember_auto("auto-123", "track-4")
    record = json.loads((tmp_path / "auto-123" / "voice.json").read_text())
    assert record["automatic"] is True and record["consent"] is False
    assert "audio" not in record and "track-4" not in v.session
    again = prints(tmp_path)
    assert again.match(clip(0))[0] == "auto-123"
    again.delete("auto-123")
    assert not (tmp_path / "auto-123" / "voice.json").exists()


def test_audio_service_loads_a_station_print_and_passes_talkers(config, bus, tmp_path):
    root = tmp_path / "people"
    v = VoicePrints(root, extract, 0.5, 5, 1, options=OPTIONS)
    seen = []
    real_harvest = v.harvest
    v.harvest = lambda pid, audio, talkers=1: (
        seen.append((pid, talkers)) or real_harvest(pid, audio, talkers)
    )
    service = AudioService(bus, config, voices=v, mic=False)
    service.clock = lambda: 0
    write_print(root, "sam", vec(0), 1.0, STATION)
    assert "sam" not in v.enrolled
    event = {"person_id": "sam", "part": "voice", "ok": True, "source": "station"}
    service._handle("enroll.result", event, 0)
    assert (
        v.sources.get("sam") == STATION
    )  # recognised on the glasses mic straight away
    # a glasses-flow voice result is not reloaded here (the audio service wrote it itself)
    service._handle(
        "enroll.result", event | {"person_id": "ana", "source": "glasses"}, 0
    )
    assert "ana" not in v.enrolled
    service.ring.append(0, np.full(3 * 16000, 1.0, np.float32))
    service.speech_intervals.append((0, 3))
    service._handle(
        "voice.harvest", {"person_id": "sam", "t0": 0, "t1": 3, "talkers": 2}, 0
    )
    service._handle("voice.harvest", {"person_id": "sam", "t0": 0, "t1": 3}, 0)
    assert seen == [("sam", 2), ("sam", 1)]
    assert len(v.adapted["sam"]) == 1


def test_audio_service_attaches_later_harvest_to_automatic_face(config, bus, tmp_path):
    root = tmp_path / "people"
    voices = VoicePrints(root, extract, 0.5, 5, 1, options=OPTIONS)
    service = AudioService(bus, config, voices=voices, mic=False)
    service.clock = lambda: 0
    service._handle(
        "enroll.result",
        {
            "person_id": "auto-abc",
            "part": "face",
            "ok": True,
            "track_id": 4,
            "source": "auto",
        },
        0,
    )
    service.ring.append(0, clip(0, 3))
    service.speech_intervals.append((0, 3))
    service._handle("voice.harvest", {"person_id": "track-4", "t0": 0, "t1": 3}, 0)
    assert voices.match(clip(0))[0] == "auto-abc"
    assert (root / "auto-abc" / "voice.json").exists()


CAM = Path(__file__).resolve().parents[2] / "models" / "cam++.onnx"


def synthetic_voice(f0, seed, seconds=5.0):
    """A buzzy vowel-like sound at pitch f0 with a syllable rhythm (not a real voice)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * 16000)
    t = np.arange(n) / 16000
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.05 * np.sin(2 * np.pi * 0.7 * t))) / 16000
    x = sum(np.sin(k * phase) / k for k in range(1, 25))
    env = np.sin(2 * np.pi * 3.5 * t) > -0.3
    return (0.1 * x * env + 0.003 * rng.standard_normal(n)).astype(np.float32)


@pytest.mark.skipif(not CAM.is_file(), reason="CAM++ model not downloaded")
def test_cam_prints_do_not_depend_on_the_clip_length():
    """Without the fix two different voices cut to 2.06-2.5 s scored ~0.96 (see CAMExtractor)."""
    from attune.audio.voiceprint import CAMExtractor

    extract = CAMExtractor(str(CAM), "cpu")
    a, b = synthetic_voice(110, 1), synthetic_voice(210, 2)
    base = extract(a[:32000])
    base = base / np.linalg.norm(base)
    for n in (24000, 33000, 36000, 40000, 48000):
        va, vb = extract(a[:n]), extract(b[:n])
        va, vb = va / np.linalg.norm(va), vb / np.linalg.norm(vb)
        assert float(va @ vb) < 0.6, n  # two voices stay apart at every length
        assert float(va @ base) > 0.9, n  # one voice stays itself
