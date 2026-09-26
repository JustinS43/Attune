"""Enrolled faces and the match rules

Section 1 - Vision
TODO: V-06
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 Faces

What to build:
- Name only when best >= 0.45 and beats runner-up by >= 0.08, three times within 1 s.
- Recheck every 2 s; three failed rechecks -> 'unknown'. Never jump straight from one name to another.
- Prints live in data/people/ (gitignored). Delete removes every file for that person.

Placeholder only - no code yet (MLH: project code is written during the event).
"""
