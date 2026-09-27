"""USB serial link to the Arduino.

Finds the board by USB ID (or ``hardware.port``), opens it without toggling DTR so the
board does not reset, waits up to ``ready_timeout_s`` for ``READY``, sends ``HB`` every
``heartbeat_s`` and reconnects on its own if the cable is pulled. Runs on its own thread;
never raises into the engine when no board is present.

Because opening the port does not reset the board (the UNO R4 WiFi; an UNO R3 may still
reset, and then prints ``READY`` at boot), an already running board will not print
``READY`` again by itself. The firmware re-announces ``READY`` when the laptop's
heartbeat comes back after a loss; if a board keeps streaming ``LV``/``HB`` but no
``READY`` arrives in time, the link still comes up with firmware/driver ``"?"``.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

from . import protocol as p

logger = logging.getLogger(__name__)

# USB vendor IDs, most specific first. Arduino (UNO R4 WiFi 0x2341:0x1002 first, then any
# other Arduino board such as the UNO R3, 0x2341:0x0043 or 0x0001), then arduino.org, then
# the common USB-serial bridges used on clones.
ARDUINO_VID = 0x2341
UNO_R4_WIFI_PID = 0x1002
KNOWN_VIDS = {
    0x2341: "Arduino",
    0x2A03: "Arduino.org",
    0x1A86: "CH340",
    0x0403: "FTDI",
    0x10C4: "CP210x",
}


class Transport(Protocol):
    name: str

    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout: float) -> str | None: ...

    def close(self) -> None: ...


def rank_port(vid: int | None, pid: int | None) -> int | None:
    """Lower is better; None means 'not a board we know'."""
    if vid is None or vid not in KNOWN_VIDS:
        return None
    if vid == ARDUINO_VID:
        return 0 if pid == UNO_R4_WIFI_PID else 1
    return 2 + list(KNOWN_VIDS).index(vid)


def find_port(preferred: str | None = None) -> str | None:
    """Return the configured port if present, else the best USB match, else None."""
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover - pyserial is a dependency
        logger.error("pyserial is not installed")
        return None
    ports = list(list_ports.comports())
    if preferred:
        for info in ports:
            if info.device.lower() == preferred.lower():
                return info.device
        return None
    ranked = []
    for info in ports:
        rank = rank_port(info.vid, info.pid)
        if rank is not None:
            ranked.append((rank, info.device))
    ranked.sort()
    return ranked[0][1] if ranked else None


class SerialTransport:
    """pyserial port opened with DTR/RTS low so the board is not reset."""

    def __init__(self, port: str, baud: int = p.BAUD):
        import serial

        self.name = port
        self._serial = serial.Serial()
        self._serial.port = port
        self._serial.baudrate = baud
        self._serial.timeout = 0.05
        self._serial.write_timeout = 0.5
        # Set before open(): pyserial applies these while opening, so DTR never pulses.
        self._serial.dtr = False
        self._serial.rts = False
        self._serial.open()
        self._buffer = bytearray()

    def write_line(self, line: str) -> None:
        self._serial.write((line + "\n").encode("ascii"))

    def read_line(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                return raw.decode("ascii", errors="replace").strip("\r ")
            chunk = self._serial.read(max(1, self._serial.in_waiting))
            if chunk:
                self._buffer.extend(chunk)
                if len(self._buffer) > 4096:  # garbage without newlines
                    self._buffer.clear()
                continue
            if time.monotonic() >= deadline:
                return None

    def close(self) -> None:
        try:
            self._serial.close()
        except Exception:
            logger.debug("ignored error", exc_info=True)


class ClockMap:
    """Maps the Arduino's millis() to the shared engine clock.

    arrival = send + latency with latency >= 0, so the smallest (arrival - ms) over a
    sliding window is the best offset estimate; the window absorbs crystal drift.
    """

    def __init__(self, window_s: float = 10.0):
        self.window_s = window_s
        self.samples: deque[tuple[float, float]] = deque()
        self.offset: float | None = None

    def reset(self) -> None:
        self.samples.clear()
        self.offset = None

    def to_engine(self, ms: int, arrival: float) -> float:
        sample = arrival - ms / 1000.0
        self.samples.append((arrival, sample))
        while self.samples and arrival - self.samples[0][0] > self.window_s:
            self.samples.popleft()
        self.offset = min(s for _, s in self.samples)
        return ms / 1000.0 + self.offset


HEARTBEAT_MIN_S, HEARTBEAT_MAX_S = 0.1, 1.0  # the board stops everything after 2 s without one


def heartbeat_period(value) -> float:
    """``[hardware] heartbeat_s``, kept between 0.1 s (no flood) and 1 s (well inside the
    board's 2 s safety stop); a bad value is logged and replaced."""
    try:
        hb = float(value)
    except (TypeError, ValueError):
        logger.warning("hardware: heartbeat_s = %r is not a number; using 0.5 s", value)
        return 0.5
    if not HEARTBEAT_MIN_S <= hb <= HEARTBEAT_MAX_S:
        fixed = min(HEARTBEAT_MAX_S, max(HEARTBEAT_MIN_S, hb))
        logger.warning(
            "hardware: heartbeat_s = %s is outside %.1f..%.1f s; using %.1f s",
            value,
            HEARTBEAT_MIN_S,
            HEARTBEAT_MAX_S,
            fixed,
        )
        return fixed
    return hb


class SerialLink:
    """Owns the connection; calls ``on_message(msg, t)`` and ``on_link(state)``."""

    def __init__(
        self,
        cfg: dict,
        clock: Callable[[], float],
        on_message: Callable[[p.Message, float], None],
        on_link: Callable[[dict], None],
        connect: Callable[[], Transport | None],
        on_ready: Callable[[], None] | None = None,
    ):
        self.cfg = cfg
        self.clock = clock
        self.on_message = on_message
        self.on_link = on_link
        self.on_ready = on_ready
        self.connect = connect
        self.ready_timeout_s = float(cfg.get("ready_timeout_s", 3))
        self.heartbeat_s = heartbeat_period(cfg.get("heartbeat_s", 0.5))
        self.stale_s = float(cfg.get("stale_s", 3.0))
        self.scan_s = float(cfg.get("scan_s", 1.0))
        self.clockmap = ClockMap()
        self.transport: Transport | None = None
        self.state = {"connected": False, "firmware": None, "driver": None, "port": None}
        self.outbox: queue.Queue[str] = queue.Queue(maxsize=64)
        self.pending: dict[int, tuple[float, str]] = {}
        self.metrics = {
            "lines_in": 0,
            "lines_out": 0,
            "bad_lines": 0,
            "reconnects": 0,
            "last_ack_ms": None,
            "errors": 0,
            "board_restarts": 0,  # the board's clock went back: it reset (brown-out?)
            "ready_again": 0,  # READY while linked: a reset, or a heartbeat gap it saw
        }
        self._board_ms: int | None = None  # newest LV millis() on this connection
        self.last_error = ""
        self._n = 0
        self._n_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.send_heartbeat = True  # tests switch this off to exercise the safety stop

    # ------------------------------------------------------------ control
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hardware-link", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._close()

    @property
    def connected(self) -> bool:
        return bool(self.state["connected"])

    def next_n(self) -> int:
        with self._n_lock:
            self._n = self._n % 999_999 + 1
            return self._n

    def send(self, line: str) -> bool:
        """Queue a line for the board; dropped (False) while disconnected."""
        if not self.connected:
            return False
        try:
            self.outbox.put_nowait(line)
            return True
        except queue.Full:
            self.last_error = "outbox full"
            return False

    def pattern(self, name: str, side: str) -> int | None:
        n = self.next_n()
        return n if self.send(p.pat(n, side, name)) else None

    def stop_all(self) -> int | None:
        n = self.next_n()
        return n if self.send(p.stop(n)) else None

    # ------------------------------------------------------------ thread
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                transport = self.connect()
            except Exception as exc:  # noqa: BLE001 - busy port, permissions, unplug race
                self.last_error = f"open failed: {exc}"
                logger.info("hardware: %s", self.last_error)
                transport = None
            if transport is None:
                self._set_link(False)
                self._stop.wait(self.scan_s)
                continue
            self.transport = transport
            try:
                if self._handshake(transport):
                    self._serve(transport)
            except Exception as exc:  # noqa: BLE001 - serial errors mean "cable pulled"
                self.last_error = f"link lost: {exc}"
                logger.warning("hardware: %s", self.last_error)
            self._close()
            self._set_link(False)
            if not self._stop.is_set():
                self.metrics["reconnects"] += 1
                self._stop.wait(min(self.scan_s, 0.5))

    def _handshake(self, transport: Transport) -> bool:
        """Wait for READY; accept a board that is already streaming without it."""
        self.clockmap.reset()
        self._board_ms = None  # opening the port resets an UNO: a fresh clock is expected
        deadline = time.monotonic() + self.ready_timeout_s
        next_hb = 0.0
        alive = False
        while not self._stop.is_set() and time.monotonic() < deadline:
            if self.send_heartbeat and time.monotonic() >= next_hb:
                transport.write_line(p.hb())
                next_hb = time.monotonic() + self.heartbeat_s
            line = transport.read_line(0.05)
            if line is None:
                continue
            try:
                msg = p.parse(line)
            except p.ProtocolError:
                self.metrics["bad_lines"] += 1
                continue
            if isinstance(msg, p.Ready):
                self._ready(msg, transport.name)
                return True
            if isinstance(msg, p.Levels | p.Heartbeat):
                alive = True
        if alive:
            logger.info("hardware: board streaming without READY; linking anyway")
            self._ready(p.Ready("?", "?"), transport.name)  # type: ignore[arg-type]
            return True
        self.last_error = f"no READY within {self.ready_timeout_s:.1f} s"
        return False

    def _ready(self, msg: p.Ready, port: str) -> None:
        self.state = {
            "connected": True,
            "firmware": msg.version,
            "driver": msg.driver,
            "port": port,
        }
        self.last_error = ""
        transport = self.transport
        if transport is not None:
            for key, value in (
                ("rate", self.cfg.get("report_ms", 50)),
                ("tap_ms", self.cfg.get("tap_ms", 400)),
                ("hold_ms", self.cfg.get("hold_ms", 800)),
                ("led", self.cfg.get("led_brightness", 180)),
            ):
                transport.write_line(p.cfg(key, int(value)))
        self.on_link(dict(self.state))
        if self.on_ready:
            self.on_ready()

    def _serve(self, transport: Transport) -> None:
        last_in = time.monotonic()
        next_hb = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if self.send_heartbeat and now >= next_hb:
                transport.write_line(p.hb())
                self.metrics["lines_out"] += 1
                next_hb = now + self.heartbeat_s
            while True:
                try:
                    line = self.outbox.get_nowait()
                except queue.Empty:
                    break
                transport.write_line(line)
                self.metrics["lines_out"] += 1
                parts = line.split()
                if parts[0] in ("PAT", "STOP"):
                    self.pending[int(parts[1])] = (now, line)
            line = transport.read_line(0.02)
            if line is None:
                if time.monotonic() - last_in > self.stale_s:
                    raise TimeoutError(f"no data for {self.stale_s:.0f} s")
                continue
            last_in = time.monotonic()
            self.metrics["lines_in"] += 1
            try:
                msg = p.parse(line)
            except p.ProtocolError:
                self.metrics["bad_lines"] += 1
                logger.debug("hardware: bad line %r", line)
                continue
            self._dispatch(msg, transport)

    def _dispatch(self, msg: p.Message, transport: Transport) -> None:
        arrival = self.clock()
        t = arrival
        if isinstance(msg, p.Levels):
            self._check_board_clock(msg.ms)
            t = self.clockmap.to_engine(msg.ms, arrival)
        elif isinstance(msg, p.Ready):
            # the board rebooted (brown-out, reset button) or re-announced after a loss
            if self.connected:
                self.metrics["ready_again"] += 1
                logger.info(
                    "hardware: board said READY again (%d so far): a reset, or it missed "
                    "heartbeats for 2 s",
                    self.metrics["ready_again"],
                )
            self.clockmap.reset()
            self._ready(msg, transport.name)
            return
        elif isinstance(msg, p.Ack):
            sent = self.pending.pop(msg.n, None)
            if sent:
                self.metrics["last_ack_ms"] = round((time.monotonic() - sent[0]) * 1000, 1)
        elif isinstance(msg, p.Err):
            self.metrics["errors"] += 1
            self.last_error = f"board: {msg.text}"
            if msg.n is not None:
                self.pending.pop(msg.n, None)
            logger.warning("hardware: board error %s", msg.text)
        self.on_message(msg, t)

    def _check_board_clock(self, ms: int) -> None:
        """LV carries the board's millis(); if it jumps back the board restarted by itself."""
        last, self._board_ms = self._board_ms, ms
        if last is not None and ms + 500 < last:
            self.metrics["board_restarts"] += 1
            self.last_error = "board restarted"
            logger.warning(
                "hardware: the board restarted (its clock went from %d ms back to %d ms). "
                "If this happens when the servo moves, its 5 V supply is browning out",
                last,
                ms,
            )

    def _set_link(self, connected: bool) -> None:
        if connected == self.state["connected"] and not connected:
            return
        self.state = {
            "connected": connected,
            "firmware": self.state["firmware"],
            "driver": self.state["driver"],
            "port": None,
        }
        self.on_link(dict(self.state))

    def _close(self) -> None:
        transport, self.transport = self.transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception:
                logger.debug("ignored error", exc_info=True)
        while True:
            try:
                self.outbox.get_nowait()
            except queue.Empty:
                break
        self.pending.clear()
