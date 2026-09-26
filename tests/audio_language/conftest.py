"""Deterministic fixtures independent of devices, downloaded models and core stubs."""

import copy
import sys
import threading
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))


class Bus:
    def __init__(self):
        self.callbacks = defaultdict(list)
        self.events = []
        self.lock = threading.RLock()

    def subscribe(self, topic, callback):
        self.callbacks[topic].append(callback)
        return lambda: self.callbacks[topic].remove(callback)

    def publish(self, topic, event):
        with self.lock:
            self.events.append((topic, copy.deepcopy(event)))
        for callback in list(self.callbacks[topic]):
            callback(event)


@pytest.fixture
def bus():
    return Bus()


@pytest.fixture
def config(tmp_path):
    return {
        "clock": lambda: 0.0,
        "engine": {"data_dir": str(tmp_path)},
        "audio": {
            "sample_rate": 48000,
            "block_ms": 10,
            "device_name": "Logitech",
            "vad_start": 0.5,
            "vad_end": 0.35,
            "min_speech_ms": 250,
            "end_silence_ms": 400,
            "languages": ["en"],
            "asr_chunk_ms": 560,
            "mute_after_reply_s": 0.5,
            "max_utterance_s": 30,
            "target_rms": 0.1,
        },
        "voice": {"enroll_s": 5.0, "match_s": 1.0},
        "fusion": {"voice_match": 0.5, "side_db": 3.0},
        "alerts": {
            "smoke_score": 0.5,
            "watch_score": 0.3,
            "doorbell_score": 0.4,
            "doorbell_rest_s": 5.0,
            "realert_s": 30.0,
            "clear_quiet_s": 15.0,
            "watch_s": 10.0,
            "speech_music_block": 0.5,
            "window_s": 1.0,
            "hop_s": 0.5,
        },
        "rhythm": {
            "low_band": [470, 570],
            "high_band": [2800, 3400],
            "frame_s": 0.01,
            "purity": 0.7,
            "min_rms": 0.01,
            "relative_energy": 5.0,
            "tolerance": 0.2,
            "t3_on_s": 0.5,
            "t3_gap_s": 0.5,
            "t3_rest_s": 1.5,
            "t4_on_s": 0.1,
            "t4_gap_s": 0.1,
            "t4_rest_s": 5.0,
        },
        "llm": {
            "model": "qwen3.5:4b",
            "keep_alive": -1,
            "num_ctx": 4096,
            "temperature": 0,
            "proposal_expiry_s": 10,
            "name_confidence": 0.8,
            "timeout_s": 1.5,
            "max_queue": 32,
            "reply_context_lines": 6,
        },
        "calibration": {"profile": "test", "room_noise_s": 30, "face_min_score": 0.5},
    }
