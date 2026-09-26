"""Microphone reader

Section 2 - Audio & Language
TODO: A-01
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 Capture

What to build:
- sounddevice WASAPI, webcam mic, 48 kHz in 10 ms blocks; resample once to 16 kHz (speech) and 32 kHz (alerts).
- Falls back to the laptop mic if the webcam disappears.

Placeholder only - no code yet (MLH: project code is written during the event).
"""
