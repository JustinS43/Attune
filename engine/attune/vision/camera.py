"""Webcam reader

Section 1 - Vision
TODO: V-01
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 Capture

What to build:
- Open by name (not index 0), MJPG 1920x1080 at 30 fps via Media Foundation, one-frame buffer.
- Stamp with core.clock; on unplug publish 'camera lost' status and keep retrying (recover <= 5 s).

Placeholder only - no code yet (MLH: project code is written during the event).
"""
