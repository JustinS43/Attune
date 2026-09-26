"""Face side of enrollment, with consent.

Section 1 - Vision. TODO: V-07. Plan: section 05 "Enrolling someone, with consent".

On `enroll.start` (track_id, name, consent, consent_t) the service collects
face prints from that track for `enroll_s` seconds (5 s) while the person
turns their head a little. It keeps the `enroll_crops` (8) most varied good
prints; with fewer than `enroll_min_crops` (5) it refuses and says why.
Nothing is stored unless consent is true and carries a time.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

REASONS = {
    "small": "come closer",
    "turned": "face the camera",
    "dark": "more light",
    "blurry": "hold still",
    "lost": "stay in view",
}


@dataclass
class EnrollJob:
    track_id: int
    name: str
    consent_t: str
    start_t: float
    prints: list[np.ndarray] = field(default_factory=list)
    rejects: Counter = field(default_factory=Counter)


def most_varied(prints: np.ndarray, k: int) -> np.ndarray:
    """Pick k prints that are as different from each other as possible (farthest-point sampling)."""
    if len(prints) <= k:
        return prints
    mean = prints.mean(axis=0)
    chosen = [int(np.argmax(prints @ mean))]  # start from the most typical print
    min_dist = 1 - prints @ prints[chosen[0]]
    while len(chosen) < k:
        nxt = int(np.argmax(min_dist))
        chosen.append(nxt)
        min_dist = np.minimum(min_dist, 1 - prints @ prints[nxt])
    return prints[chosen]


def validate_request(track_id, name, consent, consent_t) -> str:
    """Returns an error message, or "" if the request can start."""
    if consent is not True or not consent_t:
        return "consent is required"
    if not isinstance(name, str) or not name.strip():
        return "a name is required"
    if track_id is None:
        return "pick a face"
    return ""


def finish(job: EnrollJob, min_crops: int, keep: int) -> tuple[np.ndarray | None, str]:
    """Returns (prints to store, "") or (None, reason for the person)."""
    if len(job.prints) >= min_crops:
        return most_varied(np.stack(job.prints), keep), ""
    if job.rejects:
        return None, REASONS.get(job.rejects.most_common(1)[0][0], "try again")
    return None, REASONS["lost"]
