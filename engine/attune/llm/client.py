"""Ollama client with a priority queue

Section 2 - Audio & Language
TODO: A-11
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 GPU memory

What to build:
- qwen3.5:4b, think false, keep_alive -1, num_ctx 4096, temperature 0, JSON schema output.
- One job at a time: translation > names > replies > descriptions. Warm up at start; report warm/cold.

Placeholder only - no code yet (MLH: project code is written during the event).
"""
