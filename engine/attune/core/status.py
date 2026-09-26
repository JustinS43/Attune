"""Status aggregation for the console's status strip.

Section 4 - Pages, Engine & Demo. TODO: P-05. Contracts: docs/contracts.md (3).

Collects every service's `status.part` plus a few engine-level readings and
publishes the aggregate once a second on the bus topic `status`; the WebSocket hub
sends it to the console as the `status` message:

    fps           vision frames processed per second (camera fps as a fallback)
    caption_delay seconds from the last spoken word to its final caption (rolling mean)
    gpu_mem_gb    GPU memory in use on the device (torch if it holds CUDA, else nvidia-smi)
    arduino       the latest `hw.link` ({connected, firmware, driver}) or null
    ollama        {ok, detail, warm} from the llm part, or null
    mic_level     loudness of the 16 kHz audio stream over the last second, dBFS
    on_battery    true / false on Windows laptops, null when unknown
    parts         {part: {ok, detail, metrics, age_s, stale}}
"""

from __future__ import annotations

import logging
import math
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from .contracts import AUDIO_BLOCK, CAPTION, HW_LINK, STATUS, STATUS_PART, get
from .ringbuffer import TimedRing

log = logging.getLogger(__name__)

STALE_S = 5.0


def on_battery() -> bool | None:
    """True when a Windows laptop runs on battery; None when unknown or not Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class SystemPowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", wintypes.BYTE),
                ("BatteryFlag", wintypes.BYTE),
                ("BatteryLifePercent", wintypes.BYTE),
                ("SystemStatusFlag", wintypes.BYTE),
                ("BatteryLifeTime", wintypes.DWORD),
                ("BatteryFullLifeTime", wintypes.DWORD),
            ]

        st = SystemPowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return None
        return {0: True, 1: False}.get(st.ACLineStatus & 0xFF)
    except Exception:  # noqa: BLE001 - status must never fail
        return None


def _gpu_mem_torch() -> float | None:
    """Device memory in use, only if torch already holds a CUDA context (never creates one)."""
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_available() and torch.cuda.is_initialized():
            free, total = torch.cuda.mem_get_info()
            return round((total - free) / 1e9, 2)
    except Exception as exc:  # noqa: BLE001
        log.debug("torch GPU memory unavailable: %s", exc)
    return None


def _gpu_mem_smi() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode == 0 and out.stdout.strip():
            return round(float(out.stdout.split()[0]) * 1.048576e-3, 2)  # MiB -> GB
    except Exception as exc:  # noqa: BLE001
        log.debug("nvidia-smi unavailable: %s", exc)
    return None


class StatusAggregator:
    """Subscribes to status inputs and publishes the aggregate `status` every second."""

    def __init__(
        self,
        bus,
        config: dict[str, Any] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        period_s: float = 1.0,
        gpu_probe_s: float = 5.0,
    ) -> None:
        self.bus, self.clock, self.period_s = bus, clock, period_s
        self.gpu_probe_s = gpu_probe_s
        self._lock = threading.Lock()
        self.parts: dict[str, dict[str, Any]] = {}
        self.hw_link: dict[str, Any] | None = None
        self._delays = TimedRing(60.0, maxlen=64)
        self._levels = TimedRing(1.0, maxlen=250)
        self._gpu: float | None = None
        self._gpu_t = -1e9
        self._battery: bool | None = None
        self._battery_t = -1e9
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unsubs: list[Callable[[], None]] = []
        self.latest: dict[str, Any] | None = None

    # ---- bus callbacks (fast) ----
    def on_part(self, ev: Any) -> None:
        part = get(ev, "part")
        if not part:
            return
        metrics = get(ev, "metrics") or {}
        with self._lock:
            self.parts[str(part)] = {
                "ok": bool(get(ev, "ok", True)),
                "detail": get(ev, "detail", "") or "",
                "metrics": dict(metrics) if isinstance(metrics, dict) else {},
                "t": self.clock(),
            }

    def on_hw_link(self, ev: Any) -> None:
        with self._lock:
            self.hw_link = {
                "connected": bool(get(ev, "connected", False)),
                "firmware": get(ev, "firmware"),
                "driver": get(ev, "driver"),
            }

    def on_caption(self, ev: Any) -> None:
        """Delay from the last word's end time to the moment its final caption appears."""
        if not get(ev, "final", False):
            return
        words = get(ev, "words") or []
        ends = [w[2] for w in words if isinstance(w, (list, tuple)) and len(w) >= 3]
        if not ends:
            return
        now = self.clock()
        delay = now - float(max(ends))
        if 0 <= delay < 30:
            self._delays.add(now, delay)

    def on_audio_block(self, ev: Any) -> None:
        if int(get(ev, "sample_rate", 0) or 0) != 16000:
            return
        samples = get(ev, "samples")
        if samples is None or len(samples) == 0:
            return
        power = float(np.mean(np.square(samples, dtype=np.float32)))
        self._levels.add(self.clock(), power)

    # ---- lifecycle ----
    def connect(self) -> None:
        """Subscribe early (before services start) so first events are not missed."""
        if self._unsubs:
            return
        self._unsubs = [
            self.bus.subscribe(STATUS_PART, self.on_part),
            self.bus.subscribe(HW_LINK, self.on_hw_link),
            self.bus.subscribe(CAPTION, self.on_caption),
            self.bus.subscribe(AUDIO_BLOCK, self.on_audio_block),
        ]

    def start(self) -> None:
        self.connect()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="status", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self.period_s):
            try:
                self.latest = self.snapshot()
                self.bus.publish(STATUS, self.latest)
            except Exception:
                log.exception("Status aggregation failed")

    # ---- the aggregate ----
    def _probe_slow(self, now: float) -> None:
        if now - self._gpu_t >= self.gpu_probe_s:
            self._gpu_t = now
            gpu = _gpu_mem_torch()
            self._gpu = gpu if gpu is not None else _gpu_mem_smi()
        if now - self._battery_t >= 10.0:
            self._battery_t = now
            self._battery = on_battery()

    def snapshot(self) -> dict[str, Any]:
        """Build the `status` message body (without type/seq)."""
        now = self.clock()
        self._probe_slow(now)
        with self._lock:
            parts = {
                name: {
                    "ok": p["ok"],
                    "detail": p["detail"],
                    "metrics": p["metrics"],
                    "age_s": round(now - p["t"], 1),
                    "stale": now - p["t"] > STALE_S,
                }
                for name, p in self.parts.items()
            }
            hw = dict(self.hw_link) if self.hw_link else None
        vm = parts.get("vision", {}).get("metrics", {})
        fps = vm.get("vision_fps")
        if fps is None:
            fps = vm.get("camera_fps")
        llm = parts.get("llm")
        ollama = (
            {"ok": llm["ok"], "detail": llm["detail"], "warm": llm["metrics"].get("warm")}
            if llm
            else None
        )
        delays = self._delays.values(now)[-10:]
        caption_delay = round(sum(delays) / len(delays), 2) if delays else None
        powers = self._levels.values(now)
        mic_level = None
        if powers:
            mic_level = round(10 * math.log10(sum(powers) / len(powers) + 1e-12), 1)
        return {
            "fps": fps,
            "caption_delay": caption_delay,
            "gpu_mem_gb": self._gpu,
            "arduino": hw,
            "ollama": ollama,
            "mic_level": mic_level,
            "on_battery": self._battery,
            "parts": parts,
        }
