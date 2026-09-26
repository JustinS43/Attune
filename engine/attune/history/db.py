"""Connection and writes

Section 3 - Hardware & Services
TODO: H-11
Contracts: docs/contracts.md
Plan: docs/attune-build-plan.html, section 05 Conversation history

What to build:
- Python's built-in sqlite3, file from config (data/history.db, gitignored). WAL mode; one writer thread.
- Delete rows older than retention_hours (24) at start-up and every 10 minutes.

Placeholder only - no code yet (MLH: project code is written during the event).
"""
