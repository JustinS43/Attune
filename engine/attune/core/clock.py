"""The engine's shared monotonic clock.

Section 4 - Pages, Engine & Demo. TODO: P-02. Contracts: docs/contracts.md.

Every timestamp on the bus (camera frames, audio blocks, captions, sensors) comes
from `now()`, so they can be compared directly. It is `time.perf_counter`, the
clock Section 1's camera and fusion already stamp with; Section 2's runtime finds
it as `attune.core.clock.now` (or `config["clock"]`).
"""

from __future__ import annotations

import time

now = time.perf_counter
"""Seconds on the shared monotonic clock (arbitrary origin)."""

START = now()


def uptime() -> float:
    """Seconds since the engine module was imported."""
    return now() - START
