"""Face tracker (ByteTrack style)

Section 1 - Vision
TODO: V-04
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 Faces

What to build:
- Overlap matching with motion prediction; a track survives 1 s unseen, then 10 s on a lost list.
- Re-identify returning faces by face print; publish vision.track_lost with the exit side.

Placeholder only - no code yet (MLH: project code is written during the event).
"""
