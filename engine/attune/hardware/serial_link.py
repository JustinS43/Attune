"""USB serial link to the Arduino

Section 3 - Hardware & Services
TODO: H-05
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 04 Messages over USB

What to build:
- Find the board by USB ID, open without toggling DTR, wait <= 3 s for READY, send HB every 0.5 s,
- reconnect automatically (<= 3 s). Publishes sensors.levels and sensors.touch.

Placeholder only - no code yet (MLH: project code is written during the event).
"""
