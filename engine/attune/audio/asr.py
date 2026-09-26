"""Captions: Nemotron 3.5 streaming via sherpa-onnx (INT8)

Section 2 - Audio & Language
TODO: A-03
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 Captions

What to build:
- 560 ms chunks (try 320/1120). Drafts and finals, punctuation, language, word times.
- Language: auto-detect or locked list from config. Level-match each utterance. No denoising.

Placeholder only - no code yet (MLH: project code is written during the event).
"""
