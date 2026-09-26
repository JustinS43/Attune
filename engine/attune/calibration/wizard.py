"""Guided venue measurements with explicit capture and confirmation steps."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from attune.audio.runtime import Worker, engine_clock

from .profile import load, save

STEPS = ("level", "mic", "you", "other", "balance", "claps", "noise", "faces")


class CalibrationService:
    """Use calibrate.step to begin capture and finish:<step> to save its measurements."""

    def __init__(self, bus, config):
        self.bus, self.config = bus, config
        self.worker = Worker(bus, "calibration", self._handle)
        self.root = Path(config["engine"]["data_dir"]) / "profiles"
        self.name = config["calibration"]["profile"]
        self.values = {}
        self.step = None
        self.power = []
        self.peaks = []
        self.levels = []
        self.faces = {}
        self.duration = 0.0

    def start(self) -> None:
        self.clock = engine_clock(self.config)
        self.values = load(self.root, self.name)
        for topic in (
            "command",
            "audio.block",
            "sensors.levels",
            "vision.tracks",
            "session.forget",
            "paused",
        ):
            self.worker.subscribe(topic)
        self.worker.start()

    def _status(self, detail: str, ok: bool = True) -> None:
        self.worker.publish(
            "status.part",
            {
                "part": "calibration",
                "ok": ok,
                "detail": detail,
                "metrics": {"step": self.step, "duration_s": self.duration, "profile": self.values},
            },
        )

    def _handle(self, topic: str, e: dict, generation: int) -> None:
        if topic in {"session.forget", "paused"}:
            self.step = None
            self.power.clear()
            self.peaks.clear()
            self.faces.clear()
            self.levels.clear()
        elif topic == "command" and e["name"] == "calibrate.step":
            args = e["args"]
            step = args["step"]
            if step in STEPS:
                self.step = step
                self.power = []
                self.peaks = []
                self.levels = []
                self.faces = {}
                self.duration = 0.0
                self._status(f"Collecting {step}; finish:{step} saves this step")
            elif step.startswith("finish:"):
                if step[7:] != self.step:
                    raise ValueError("finish step does not match active capture")
                self.finish(args.get("measurements", {}))
        elif self.step and topic == "audio.block" and e["sample_rate"] == 16000:
            samples = np.asarray(e["samples"])
            if len(samples):
                # Bound calibration capture to two minutes; store summaries, never PCM.
                if self.duration >= 120:
                    return
                self.power.append(float(np.mean(samples * samples)))
                self.peaks.append(float(np.max(np.abs(samples))))
                self.duration += len(samples) / 16000
        elif self.step == "balance" and topic == "sensors.levels" and not e["motor_on"]:
            if len(self.levels) < 2400:
                self.levels.append((e["left"], e["right"]))
        elif self.step == "faces" and topic == "vision.tracks":
            for track in e["tracks"]:
                if track.get("person_id") and track.get("status") == "enrolled":
                    key = str(track["person_id"])
                    self.faces[key] = min(self.faces.get(key, 1.0), track["match_score"])

    def finish(self, measurements: dict) -> None:
        """Validate and persist a step; manual observations must be explicitly supplied."""
        step = self.step
        values = dict(self.values)
        rms = math.sqrt(float(np.mean(self.power))) if self.power else 0
        db = 20 * math.log10(max(rms, 1e-9))
        if step == "level":
            if measurements.get("confirmed") is not True:
                raise ValueError("confirm the camera is level using the lens view")
            values["level_confirmed"] = True
        elif step in {"mic", "you", "other"}:
            if not self.power or rms <= 0:
                raise ValueError("record speech before finishing")
            if step == "mic":
                values["mic_peak_dbfs"] = 20 * math.log10(max(self.peaks))
            else:
                values[f"{step}_dbfs"] = db
                if "you_dbfs" in values and "other_dbfs" in values:
                    if values["you_dbfs"] <= values["other_dbfs"]:
                        raise ValueError("wearer must be louder; repeat both speech captures")
                    values["you_threshold_dbfs"] = (values["you_dbfs"] + values["other_dbfs"]) / 2
        elif step == "balance":
            if not self.levels:
                raise ValueError("no clean sensor observations")
            left, right = np.median(self.levels, axis=0)
            if min(left, right) <= 0:
                raise ValueError("both sensors need a measurable signal")
            values["left_gain"], values["right_gain"] = float(right / left), 1.0
        elif step == "claps":
            audio, video = measurements.get("audio_times", []), measurements.get("video_times", [])
            if (
                len(audio) != 3
                or len(video) != 3
                or not all(math.isfinite(t) for t in audio + video)
            ):
                raise ValueError("supply three annotated sound and hands-meeting timestamps")
            values["av_offset_s"] = float(np.median(np.asarray(audio) - np.asarray(video)))
        elif step == "noise":
            if self.duration < self.config["calibration"]["room_noise_s"]:
                raise ValueError("collect the full configured room-noise interval")
            values["noise_rms"] = rms
        elif step == "faces":
            if not self.faces or measurements.get("distances_checked") != [1, 2, 3]:
                raise ValueError("check each enrolled teammate at 1, 2 and 3 m")
            if min(self.faces.values()) < self.config["calibration"]["face_min_score"]:
                raise ValueError("re-enroll weak faces in the venue lighting")
            # Save quality measurements only, without persistent person identities.
            values["face_scores"] = sorted(self.faces.values())
        else:
            raise ValueError("no active calibration step")
        values["complete_steps"] = list(dict.fromkeys(values.get("complete_steps", []) + [step]))
        save(self.root, self.name, values)
        self.values = values
        self._status(f"Saved {step}")
        self.step = None

    def stop(self) -> None:
        self.worker.stop()
