"""Conversation history package: local SQLite, rows deleted after 24 h (TODO H-10..H-12).

Stores text only: never audio, video, face prints or voice prints.
"""

from .service import HistoryService

__all__ = ["HistoryService"]
