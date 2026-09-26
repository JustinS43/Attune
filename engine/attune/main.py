"""Starts and stops every service

Section 4 - Pages, Engine & Demo
TODO: P-02
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 03

What to build:
- Load config, create the bus and clock, then create each section's service in this order:
- hardware, audio, vision, fusion, alerts, llm, speech_out, history, server.
- Every service follows the Service convention in docs/contracts.md (start / stop / status).
- A service that fails to start is reported in status and the rest keep running (fallback ladder).

Placeholder only - no code yet (MLH: project code is written during the event).
"""
