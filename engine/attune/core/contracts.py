"""Event and message types - the code copy of docs/contracts.md

Section 4 - Pages, Engine & Demo
TODO: P-01
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05

What to build:
- One dataclass per bus event and WebSocket message listed in docs/contracts.md, same names and fields.
- This file and docs/contracts.md change together, in a [shared] PR, and only by adding fields.

Placeholder only - no code yet (MLH: project code is written during the event).
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AudioBlock:
    """Local-only mono PCM; t is the first sample on the shared clock."""

    t: float
    sample_rate: int
    samples: Any


@dataclass(frozen=True)
class EnrollResult:
    """Echo track_id so voice enrollment cannot reuse another person's consent."""

    person_id: str
    part: str
    ok: bool
    reason: str
    track_id: int | None = None
