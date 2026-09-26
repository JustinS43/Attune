import pytest
from attune.hardware import protocol as p

ROUND_TRIP = [
    "READY 1.0.0 TB6612",
    "READY 1.0.0 L298",
    "READY 1.0.0 NONE",
    "LV 123456 17 950 1",
    "LV 0 0 0 0",
    "TOUCH TAP",
    "TOUCH HOLD",
    "TOUCH DOUBLE",
    "TOUCH TRIPLE",
    "HB 1000",
    "HB",
    "ACK 7",
    "ERR driver missing",
    "PAT 3 L T3",
    "PAT 4 B BELL",
    "PAT 5 R NAME",
    "STOP 9",
    "MX HEART",
    "MX ALERT_L",
    "MX LOST",
    "CFG rate 50",
    "CFG tap_ms 400",
    "CFG hold_ms 800",
    "CFG led 180",
]


@pytest.mark.parametrize("line", ROUND_TRIP)
def test_round_trip(line):
    assert p.format_message(p.parse(line)) == line


def test_parsed_fields():
    assert p.parse("READY 1.2.3 TB6612") == p.Ready("1.2.3", "TB6612")
    assert p.parse("LV 50 10 20 0") == p.Levels(50, 10, 20, False)
    assert p.parse("TOUCH DOUBLE") == p.Touch("double")
    assert p.parse("touch triple") == p.Touch("triple")
    assert p.parse("HB 2000") == p.Heartbeat(2000)
    assert p.parse("ACK 12") == p.Ack(12)
    assert p.parse("ERR 5 bad pattern") == p.Err("5 bad pattern", 5)
    assert p.parse("ERR driver missing").n is None
    assert p.parse("PAT 1 r ok") == p.Pattern(1, "R", "OK")
    assert p.parse("  LV 1 2 3 1\r\n").motor_on is True


def test_levels_clamped():
    assert p.parse("LV 1 -5 5000 0") == p.Levels(1, 0, 1023, False)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "HELLO",
        "READY 1.0",
        "READY 1 FOO",
        "LV 1 2 3",
        "LV a 2 3 0",
        "LV 1 2 3 2",
        "TOUCH SWIPE",
        "ACK x",
        "PAT 1 X T3",
        "PAT 1 L SIREN",
        "PAT L T3",
        "STOP",
        "MX SMILE",
        "CFG speed 3",
        "CFG rate fast",
        "HB 1 2",
    ],
)
def test_malformed(line):
    with pytest.raises(p.ProtocolError):
        p.parse(line)


def test_helpers():
    assert p.pat(2, "l", "t4") == "PAT 2 L T4"
    assert p.stop(3) == "STOP 3"
    assert p.hb() == "HB"
    assert p.mx("pause") == "MX PAUSE"
    assert p.cfg("led", 99) == "CFG led 99"
    with pytest.raises(p.ProtocolError):
        p.pat(1, "L", "LOST")  # LOST is played by the board itself, not sent
    with pytest.raises(p.ProtocolError):
        p.cfg("volume", 1)


@pytest.mark.parametrize(
    "side,code",
    [("left", "L"), ("right", "R"), ("none", "B"), (None, "B"), ("L", "L"), ("B", "B")],
)
def test_side_code(side, code):
    assert p.side_code(side) == code


def test_firmware_header_matches():
    """protocol.h must spell the same patterns, icons and CFG keys."""
    from pathlib import Path

    header = (
        Path(__file__).resolve().parents[2] / "firmware" / "attune_rig" / "protocol.h"
    )
    text = header.read_text(encoding="utf-8")
    for word in (
        *p.PATTERNS,
        *p.ICONS,
        *p.CFG_KEYS,
        *p.DRIVERS,
        "READY",
        "LV",
        "TOUCH",
        "ACK",
        "ERR",
        "STOP",
        "MX",
        "CFG",
        "PAT",
    ):
        assert f'"{word}"' in text, word
