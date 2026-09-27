"""FusionService: runs SpeakerFusion on the bus.

Section 1 - Vision (fusion). TODO: V-09. Contracts: docs/contracts.md.

Subscribes to vision, audio, sensor and name events, ticks 15 times a second,
and publishes `scene` (every tick and immediately when the speaker changes),
`caption` (transcripts with their speaker), `caption.retract` (segment ids a later
draft dropped) and `voice.harvest`.

V-32: with cloud captions, `speaker.cloud` tags and `cloud.state` feed the fusion's
CloudTags ([cloud] settings); without them nothing changes.

A-21: every tick notes how many visible faces are talking; a harvest waits
`[voice] harvest_lag_s` (so its audio has reached the audio side) and leaves with
`talkers`, the most faces talking at once over its span (see fusion/harvest.py).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from ..vision import types as T
from ..vision.settings import load_settings
from .cloud_tags import CloudTags, CloudTagSettings
from .harvest import DelayedHarvests, TalkerLog
from .speaker import SpeakerFusion

log = logging.getLogger(__name__)


class FusionService:
    def __init__(
        self,
        bus,
        config: dict[str, Any] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.bus = bus
        self.clock = clock
        _, self.settings = load_settings(config)
        cloud = CloudTags(CloudTagSettings.from_config((config or {}).get("cloud")))
        self.fusion = SpeakerFusion(self.settings, cloud)  # V-32: cloud tags, used only when on
        voice = (config or {}).get("voice") or {}
        self.talkers = TalkerLog()
        self.harvests = DelayedHarvests(float(voice.get("harvest_lag_s", 0.4)))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._paused = False

    def start(self) -> None:
        f = self.fusion
        subs = {
            T.VISION_TRACKS: f.on_tracks,
            T.VISION_TRACK_LOST: f.on_track_lost,
            T.VISION_APPEARANCE: f.on_appearance,
            T.VISION_DESCRIPTION: f.on_description,
            T.AUDIO_VAD: f.on_vad,
            T.AUDIO_BLOCK: f.on_audio_block,
            T.AUDIO_LEVEL: f.on_audio_level,
            T.SENSORS_LEVELS: f.on_sensor_levels,
            T.AUDIO_VOICE_MATCH: f.on_voice_match,
            T.NAME_PROPOSAL: f.on_name_proposal,
        }
        for topic, handler in subs.items():
            self.bus.subscribe(topic, self._locked(handler))
        self.bus.subscribe(T.AUDIO_TRANSCRIPT, self._on_transcript)
        self.bus.subscribe(T.SPEAKER_CLOUD, self._on_cloud_words)
        self.bus.subscribe(T.CLOUD_STATE, self._locked(f.on_cloud_state))
        self.bus.subscribe(T.SESSION_FORGET, self._locked(self._on_forget))
        self.bus.subscribe(T.PAUSED, self._on_paused)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fusion", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _locked(self, handler):
        def wrapped(ev):
            with self._lock:
                handler(ev)

        return wrapped

    def _on_transcript(self, ev) -> None:
        with self._lock:
            self.fusion.on_transcript(ev, self.clock())

    def _on_cloud_words(self, ev) -> None:
        with self._lock:
            self.fusion.on_cloud_words(ev, self.clock())

    def _on_forget(self, ev) -> None:
        self.fusion.forget_session()
        self.harvests.clear()
        self.talkers.clear()

    def _on_paused(self, ev) -> None:
        self._paused = bool(T.get(ev, "paused", False))

    def talking_faces(self, now: float) -> int:
        """Visible faces talking now: Light-ASD's verdict where it is fresh, else the lip checks."""
        f = self.fusion
        covered = f.asd_gate.covered(f, now)
        n = 0
        for tid, tr in f.tracks.items():
            if now - tr.t > 0.5:
                continue
            if tid in covered:
                n += bool(covered[tid].talking)
            else:
                n += bool(tr.talking or tr.probable)
        return n

    def _run(self) -> None:
        period = 1.0 / self.settings.rate_hz
        last_status = 0.0
        next_tick = time.perf_counter()
        while True:
            next_tick += period
            delay = next_tick - time.perf_counter()
            if delay < 0:  # fell behind: don't try to catch up with a burst of ticks
                next_tick = time.perf_counter()
                delay = 0
            if self._stop.wait(delay):
                return
            now = self.clock()
            try:
                with self._lock:
                    before = self.fusion.current
                    scene, captions, harvest = self.fusion.tick(now)
                    retractions = self.fusion.take_retractions()
                    speaker = self.fusion.current
                    self.talkers.note(now, self.talking_faces(now))
                    if harvest is not None:
                        self.harvests.add(harvest)
                    ready = self.harvests.due(now, self.talkers)
                self.bus.publish(T.SCENE, scene)
                for cap in captions:
                    self.bus.publish(T.CAPTION, cap)
                for gone in retractions:
                    self.bus.publish(T.CAPTION_RETRACT, gone)
                for done in ready:
                    if not self._paused:
                        self.bus.publish(T.VOICE_HARVEST, done)
                if speaker is not before and log.isEnabledFor(logging.DEBUG):
                    log.debug("Speaker: %s", speaker)
                if now - last_status >= 1.0:
                    last_status = now
                    self.bus.publish(
                        T.STATUS_PART,
                        T.StatusPart(
                            "fusion",
                            True,
                            "",
                            {
                                "speaker": speaker.kind if speaker else None,
                                "faces": len(scene.faces),
                            },
                        ),
                    )
            except Exception:
                log.exception("Fusion tick failed")
