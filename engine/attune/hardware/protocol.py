"""Parse and format the Arduino serial text lines (docs/contracts.md section 6).

Plain text, 115200 baud, ``\\n`` endings. The same names live in
``firmware/attune_rig/protocol.h``; keep the two files in step.

Arduino -> laptop: ``READY``, ``LV``, ``TOUCH``, ``HB``, ``ACK``, ``ERR``.
Laptop -> Arduino: ``PAT``, ``STOP``, ``HB``, ``MX``, ``CFG``.
"""

from __future__ import annotations

from dataclasses import dataclass

BAUD = 115200

PATTERNS = ("T3", "T4", "BELL", "NAME", "OK", "NO")
SIDES = ("L", "R", "B")
# ALERT_B (alert with no known side) is a firmware extension; see docs/hardware/wiring.md.
ICONS = ("HEART", "ALERT_L", "ALERT_R", "ALERT_B", "PAUSE", "LOST")
DRIVERS = ("TB6612", "L298", "NONE")
GESTURES = {"TAP": "tap", "HOLD": "hold", "DOUBLE": "double", "TRIPLE": "triple"}
CFG_KEYS = ("rate", "tap_ms", "hold_ms", "led")


class ProtocolError(ValueError):
    """A line that does not match the protocol."""


# ---------------------------------------------------------------- inbound


@dataclass(frozen=True)
class Ready:
    version: str
    driver: str


@dataclass(frozen=True)
class Levels:
    ms: int
    left: int
    right: int
    motor_on: bool


@dataclass(frozen=True)
class Touch:
    gesture: str  # tap, hold, double, triple (bus spelling)


@dataclass(frozen=True)
class Heartbeat:
    ms: int | None = None  # None for the laptop's bare "HB"


@dataclass(frozen=True)
class Ack:
    n: int


@dataclass(frozen=True)
class Err:
    text: str
    n: int | None = None  # set when the text starts with a command number


# --------------------------------------------------------------- outbound


@dataclass(frozen=True)
class Pattern:
    n: int
    side: str
    name: str


@dataclass(frozen=True)
class Stop:
    n: int


@dataclass(frozen=True)
class Matrix:
    icon: str


@dataclass(frozen=True)
class Config:
    key: str
    value: int


Message = Ready | Levels | Touch | Heartbeat | Ack | Err | Pattern | Stop | Matrix | Config


def _int(text: str, what: str) -> int:
    try:
        return int(text)
    except ValueError as exc:
        raise ProtocolError(f"bad {what}: {text!r}") from exc


def _clamp_level(value: int) -> int:
    return max(0, min(1023, value))


def parse(line: str) -> Message:
    """Parse one line in either direction. Raises ProtocolError on anything malformed."""
    parts = line.strip().split()
    if not parts:
        raise ProtocolError("empty line")
    head, args = parts[0].upper(), parts[1:]
    if head == "READY":
        if len(args) != 2:
            raise ProtocolError(f"READY needs version and driver: {line!r}")
        driver = args[1].upper()
        if driver not in DRIVERS:
            raise ProtocolError(f"unknown driver {args[1]!r}")
        return Ready(args[0], driver)
    if head == "LV":
        if len(args) != 4:
            raise ProtocolError(f"LV needs 4 fields: {line!r}")
        motor = _int(args[3], "motor flag")
        if motor not in (0, 1):
            raise ProtocolError(f"bad motor flag {args[3]!r}")
        return Levels(
            _int(args[0], "ms"),
            _clamp_level(_int(args[1], "left")),
            _clamp_level(_int(args[2], "right")),
            bool(motor),
        )
    if head == "TOUCH":
        if len(args) != 1 or args[0].upper() not in GESTURES:
            raise ProtocolError(f"bad TOUCH: {line!r}")
        return Touch(GESTURES[args[0].upper()])
    if head == "HB":
        if not args:
            return Heartbeat(None)
        if len(args) != 1:
            raise ProtocolError(f"bad HB: {line!r}")
        return Heartbeat(_int(args[0], "ms"))
    if head == "ACK":
        if len(args) != 1:
            raise ProtocolError(f"bad ACK: {line!r}")
        return Ack(_int(args[0], "n"))
    if head == "ERR":
        text = line.strip()[3:].strip()
        n = None
        if args and args[0].lstrip("-").isdigit():
            n = int(args[0])
        return Err(text, n)
    if head == "PAT":
        if len(args) != 3:
            raise ProtocolError(f"PAT needs n, side and name: {line!r}")
        side, name = args[1].upper(), args[2].upper()
        if side not in SIDES:
            raise ProtocolError(f"bad side {args[1]!r}")
        if name not in PATTERNS:
            raise ProtocolError(f"unknown pattern {args[2]!r}")
        return Pattern(_int(args[0], "n"), side, name)
    if head == "STOP":
        if len(args) != 1:
            raise ProtocolError(f"bad STOP: {line!r}")
        return Stop(_int(args[0], "n"))
    if head == "MX":
        if len(args) != 1 or args[0].upper() not in ICONS:
            raise ProtocolError(f"bad MX: {line!r}")
        return Matrix(args[0].upper())
    if head == "CFG":
        if len(args) != 2:
            raise ProtocolError(f"CFG needs key and value: {line!r}")
        if args[0] not in CFG_KEYS:
            raise ProtocolError(f"unknown CFG key {args[0]!r}")
        return Config(args[0], _int(args[1], "value"))
    raise ProtocolError(f"unknown message {head!r}")


def format_message(msg: Message) -> str:
    """Format a message as one line, without the trailing newline."""
    match msg:
        case Ready(version, driver):
            return f"READY {version} {driver}"
        case Levels(ms, left, right, motor_on):
            return f"LV {ms} {left} {right} {int(motor_on)}"
        case Touch(gesture):
            return f"TOUCH {gesture.upper()}"
        case Heartbeat(None):
            return "HB"
        case Heartbeat(ms):
            return f"HB {ms}"
        case Ack(n):
            return f"ACK {n}"
        case Err(text, _):
            return f"ERR {text}"
        case Pattern(n, side, name):
            return f"PAT {n} {side} {name}"
        case Stop(n):
            return f"STOP {n}"
        case Matrix(icon):
            return f"MX {icon}"
        case Config(key, value):
            return f"CFG {key} {value}"
    raise ProtocolError(f"cannot format {msg!r}")


# Short helpers for the laptop side.
def pat(n: int, side: str, name: str) -> str:
    """``PAT <n> <L/R/B> <name>``; validates side and name."""
    side, name = side.upper(), name.upper()
    if side not in SIDES:
        raise ProtocolError(f"bad side {side!r}")
    if name not in PATTERNS:
        raise ProtocolError(f"unknown pattern {name!r}")
    return format_message(Pattern(n, side, name))


def stop(n: int) -> str:
    return format_message(Stop(n))


def hb() -> str:
    return "HB"


def mx(icon: str) -> str:
    icon = icon.upper()
    if icon not in ICONS:
        raise ProtocolError(f"unknown icon {icon!r}")
    return format_message(Matrix(icon))


def cfg(key: str, value: int) -> str:
    if key not in CFG_KEYS:
        raise ProtocolError(f"unknown CFG key {key!r}")
    return format_message(Config(key, int(value)))


def side_code(side: str | None) -> str:
    """Map bus sides (left/right/none/L/R/B) to the serial L/R/B."""
    s = (side or "B").strip().lower()
    return {"left": "L", "l": "L", "right": "R", "r": "R"}.get(s, "B")
