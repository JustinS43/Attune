"""FakeArduino: an in-process stand-in for the rig that speaks the same serial protocol.

Used when ``hardware.simulate = true`` or ``ATTUNE_SIMULATE_HARDWARE=1``, and by the tests.
It mirrors firmware/attune_rig: READY on open, LV every ``rate`` ms with noisy levels,
HB every second, ACK/ERR for commands, the 2 s heartbeat-loss safety stop with the LOST
icon, the pattern timings from patterns.h (so ``motor_on`` is realistic) and touches
injected with :meth:`FakeArduino.inject_touch`.
"""

from __future__ import annotations

import logging
import queue
import random
import threading
import time
from collections.abc import Callable

from . import protocol as p

logger = logging.getLogger(__name__)

FIRMWARE_VERSION = "1.0.0-sim"

# (duration ms, light level 0..1 or "fade", motor on) - mirrors firmware/attune_rig/patterns.h
Step = tuple[int, float | str, bool]
PATTERN_STEPS: dict[str, tuple[list[Step], bool]] = {
    # name: (steps, repeats)
    "T3": (
        [
            (450, 1.0, True),
            (50, 1.0, False),
            (500, 0.0, False),
            (450, 1.0, True),
            (50, 1.0, False),
            (500, 0.0, False),
            (450, 1.0, True),
            (50, 1.0, False),
            (1500, 0.0, False),
        ],
        True,
    ),
    "T4": (
        [
            (80, 1.0, True),
            (20, 1.0, False),
            (100, 0.0, False),
            (80, 1.0, True),
            (20, 1.0, False),
            (100, 0.0, False),
            (80, 1.0, True),
            (20, 1.0, False),
            (100, 0.0, False),
            (80, 1.0, True),
            (20, 1.0, False),
            (5000, 0.0, False),
        ],
        True,
    ),
    "BELL": (
        [(120, 1.0, True), (30, 1.0, False), (150, 0.0, False), (120, 1.0, True), (30, 1.0, False)],
        False,
    ),
    "NAME": ([(1000, "fade", False)], False),
    "OK": ([(80, 1.0, True), (20, 1.0, False)], False),
    "NO": ([(40, 0.0, True), (80, 0.0, False), (40, 0.0, True)], False),
    "LOST": ([(100, 0.2, False), (1900, 0.0, False)], True),
}


class SimulatedUnplug(OSError):
    """Raised by the fake when the 'cable' has been pulled."""


class _Player:
    """Plays one pattern's step list against a clock (ms)."""

    def __init__(self, name: str, side: str, start_ms: int):
        self.name, self.side, self.start_ms = name, side, start_ms
        self.steps, self.repeats = PATTERN_STEPS[name]
        self.total = sum(s[0] for s in self.steps)

    def state(self, now_ms: int) -> tuple[float, bool, bool]:
        """(light, motor, finished) at now_ms."""
        elapsed = now_ms - self.start_ms
        if elapsed >= self.total:
            if not self.repeats:
                return 0.0, False, True
            elapsed %= self.total
        acc = 0
        for dur, light, motor in self.steps:
            if elapsed < acc + dur:
                if light == "fade":
                    frac = (elapsed - acc) / dur
                    light = 1.0 - abs(2 * frac - 1)
                return float(light), motor, False
            acc += dur
        return 0.0, False, False


class FakeArduino:
    """Implements the Transport interface (write_line / read_line / close)."""

    name = "simulator"

    def __init__(
        self,
        driver: str = "TB6612",
        seed: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        tick_s: float = 0.005,
        heartbeat_timeout_s: float = 2.0,
    ):
        self.driver = driver
        self.clock = clock
        self.tick_s = tick_s
        self.heartbeat_timeout_ms = int(heartbeat_timeout_s * 1000)
        self.rng = random.Random(seed)
        self.out: queue.Queue[str] = queue.Queue()
        self.received: list[str] = []
        self.cfg = {"rate": 50, "tap_ms": 400, "hold_ms": 800, "led": 180}
        self.icon = "LOST"
        self.background: _Player | None = None
        self.foreground: _Player | None = None
        self.lost_player: _Player | None = None
        self.linked = False
        self.unplugged = False
        self.mute_heartbeat_out = False
        self.sound = {"left": 0.0, "right": 0.0}  # injected loudness, 0..1023 added
        self._t0 = clock()
        self._last_hb_ms = -(10**9)
        self._next_lv = 0
        self._next_hb = 1000
        self._window_motor = False
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fake-arduino", daemon=True)
        self._thread.start()
        self._emit(p.format_message(p.Ready(FIRMWARE_VERSION, driver)))

    # ------------------------------------------------------ Transport API
    def write_line(self, line: str) -> None:
        if self.unplugged or self._closed.is_set():
            raise SimulatedUnplug("simulated cable pulled")
        with self._lock:
            self.received.append(line)
            self._handle(line.strip())

    def read_line(self, timeout: float) -> str | None:
        if self.unplugged:
            raise SimulatedUnplug("simulated cable pulled")
        try:
            return self.out.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed.set()

    # ------------------------------------------------------ test/demo hooks
    def inject_touch(self, gesture: str) -> None:
        """Pretend the touch pad saw a gesture: tap, hold, double or triple."""
        g = gesture.strip().upper()
        if g not in p.GESTURES:
            raise ValueError(f"unknown gesture {gesture!r}")
        self._emit(f"TOUCH {g}")

    def inject_sound(self, left: float = 0.0, right: float = 0.0) -> None:
        """Add a steady loudness (0..1023) to each side, e.g. to fake a sound from the left."""
        self.sound = {"left": float(left), "right": float(right)}

    def unplug(self) -> None:
        self.unplugged = True

    @property
    def motor_on(self) -> bool:
        with self._lock:
            return self._state(self._ms())[1]

    @property
    def active_pattern(self) -> str | None:
        with self._lock:
            for player in (self.foreground, self.background):
                if player is not None:
                    return player.name
            return None

    # ------------------------------------------------------ internals
    def _ms(self) -> int:
        return int((self.clock() - self._t0) * 1000)

    def _emit(self, line: str) -> None:
        if not self._closed.is_set():
            self.out.put(line)

    def _handle(self, line: str) -> None:
        now = self._ms()
        try:
            msg = p.parse(line)
        except p.ProtocolError as exc:
            parts = line.split()
            n = parts[1] if len(parts) > 1 and parts[1].isdigit() else None
            self._emit(f"ERR {n} {exc}" if n else f"ERR {exc}")
            return
        if isinstance(msg, p.Heartbeat):
            if not self.linked:
                self.linked = True
                self.lost_player = None
                self.icon = "HEART"
                # every (re)link announces READY again, like the firmware, because the
                # laptop opens the port without resetting the board (see firmware README)
                self._emit(p.format_message(p.Ready(FIRMWARE_VERSION, self.driver)))
            self._last_hb_ms = now
        elif isinstance(msg, p.Pattern):
            player = _Player(msg.name, msg.side, now)
            if player.repeats:
                self.background, self.foreground = player, None
            else:
                self.foreground = player
            self._emit(f"ACK {msg.n}")
        elif isinstance(msg, p.Stop):
            self.background = self.foreground = None
            self._emit(f"ACK {msg.n}")
        elif isinstance(msg, p.Matrix):
            self.icon = msg.icon
        elif isinstance(msg, p.Config):
            self.cfg[msg.key] = msg.value
        else:
            self._emit(f"ERR unexpected {line.split()[0]}")

    def _state(self, now: int) -> tuple[float, bool]:
        for attr in ("foreground", "background", "lost_player"):
            player = getattr(self, attr)
            if player is None:
                continue
            light, motor, finished = player.state(now)
            if finished:
                setattr(self, attr, None)
                continue
            return light, motor
        return 0.0, False

    def _run(self) -> None:
        while not self._closed.wait(self.tick_s):
            with self._lock:
                now = self._ms()
                _, motor = self._state(now)
                self._window_motor |= motor
                if self.linked and now - self._last_hb_ms > self.heartbeat_timeout_ms:
                    # safety stop: laptop silent for 2 s
                    self.linked = False
                    self.background = self.foreground = None
                    self.icon = "LOST"
                    self.lost_player = _Player("LOST", "B", now)
                if now >= self._next_lv:
                    self._next_lv = max(self._next_lv + max(10, self.cfg["rate"]), now - 100)
                    left, right = self._levels(self._window_motor)
                    self._emit(p.format_message(p.Levels(now, left, right, self._window_motor)))
                    self._window_motor = motor
                if now >= self._next_hb:
                    self._next_hb += 1000
                    if not self.mute_heartbeat_out:
                        self._emit(p.format_message(p.Heartbeat(now)))

    def _levels(self, motor: bool) -> tuple[int, int]:
        """Peak-to-peak loudness: room noise, occasional bursts, motor noise."""
        out = []
        for side in ("left", "right"):
            level = 18 + self.rng.gauss(0, 4) + self.sound[side]
            if self.rng.random() < 0.02:
                level += self.rng.uniform(40, 220)  # a clap or a voice nearby
            if motor:
                level += 35
            out.append(int(max(0, min(1023, level))))
        return out[0], out[1]
