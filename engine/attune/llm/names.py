"""Learning names from introductions

Section 2 - Audio & Language
TODO: A-12
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 Learning names

What to build:
- Phrase filter first, then JSON answer (is_intro, name, whose, confidence) with ~8 examples, half traps.
- 1-3 words, not on the stop-list, never from 'You'. Publishes name.proposal; expires after 10 s.

Placeholder only - no code yet (MLH: project code is written during the event).
"""
