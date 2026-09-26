"""Lip-motion score per face (MediaPipe Face Landmarker on crops)

Section 1 - Vision
TODO: V-08
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 Who's talking

What to build:
- Mouth-open ratio = landmarks 13/14 over 78/308; score = 1 s rolling std.
- Talking >= 0.03, uncertain 0.015-0.03 (verified on the film clips).

Placeholder only - no code yet (MLH: project code is written during the event).
"""
