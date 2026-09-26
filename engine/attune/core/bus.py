"""In-process publish/subscribe bus

Section 4 - Pages, Engine & Demo
TODO: P-02
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 03

What to build:
- Thread-safe publish(topic, event) and subscribe(topic, callback) for worker threads and the asyncio loop.
- Topics are the ones in docs/contracts.md. Slow subscribers must not block publishers.

Placeholder only - no code yet (MLH: project code is written during the event).
"""
