"""Decides who is talking and builds the Scene

Section 1 - Vision
TODO: V-09
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 Who's talking

What to build:
- Cases in order: You (sensor levels), visible speaker (>= 0.03 + in time), probable (dashed),
- off-screen (voice match >= 0.5, side from exit or sensors). Hold 0.5 s; switch only at 1.5x.
- Assigns speakers to audio.transcript -> publishes caption and scene (15 times a second).

Placeholder only - no code yet (MLH: project code is written during the event).
"""
