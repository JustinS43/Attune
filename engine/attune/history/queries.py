"""Reads for the history panel (TODO H-12).

Every function takes an open sqlite3 connection (row_factory = sqlite3.Row) and returns
plain JSON-ready data. Timeline rows carry the contract fields
``{t, kind, speaker_label, text, translation, lang}``; ``t`` is epoch seconds (history
spans engine restarts, so the engine clock cannot be used across sessions). Extra fields
(``id, session_id, engine_t, utt_id, alert_kind, side``) are additive.
"""

from __future__ import annotations

import re
import sqlite3

ROW_SELECT = (
    "SELECT r.id, r.session_id, r.wall AS t, r.engine_t, r.kind, r.utt_id, r.speaker_label, "
    "r.text, r.translation, r.lang, r.alert_kind, r.side FROM rows r"
)


def _rows(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def latest_session(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT session_id FROM sessions ORDER BY started_t DESC LIMIT 1").fetchone()
    return row["session_id"] if row else None


def sessions(conn: sqlite3.Connection) -> list[dict]:
    """Newest first: ``[{session_id, started_t, ended_t, lines}]``."""
    return _rows(
        conn.execute(
            "SELECT s.session_id, s.started_t, s.ended_t, "
            "(SELECT count(*) FROM rows r WHERE r.session_id = s.session_id) AS lines "
            "FROM sessions s ORDER BY s.started_t DESC"
        )
    )


def timeline(conn: sqlite3.Connection, session_id: str, limit: int = 5000) -> list[dict]:
    return _rows(
        conn.execute(
            f"{ROW_SELECT} WHERE r.session_id = ? ORDER BY r.wall, r.id LIMIT ?",
            (session_id, limit),
        )
    )


def fts_query(q: str) -> str:
    """Turn free text into a safe FTS5 query: every word must appear (prefix match)."""
    words = re.findall(r"\w+", q, flags=re.UNICODE)
    return " ".join(f'"{w}"*' for w in words)


def search(
    conn: sqlite3.Connection,
    q: str = "",
    person: str | None = None,
    session_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Full-text search, optionally filtered by speaker label and session; newest first."""
    where, params = [], []
    match = fts_query(q or "")
    if match:
        where.append("r.id IN (SELECT rowid FROM rows_fts WHERE rows_fts MATCH ?)")
        params.append(match)
    elif q and q.strip():
        return []  # only punctuation: nothing can match
    if person:
        where.append("r.speaker_label = ? COLLATE NOCASE")
        params.append(person)
    if session_id:
        where.append("r.session_id = ?")
        params.append(session_id)
    sql = ROW_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY r.wall DESC, r.id DESC LIMIT ?"
    params.append(limit)
    return _rows(conn.execute(sql, params))


def missed(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """'Sounds you missed': the alerts of that session, in time order."""
    return _rows(
        conn.execute(
            f"{ROW_SELECT} WHERE r.session_id = ? AND r.kind = 'alert' ORDER BY r.wall, r.id",
            (session_id,),
        )
    )


def talktime(conn: sqlite3.Connection, session_id: str) -> dict[str, list[dict]]:
    """Seconds spoken per person per minute since the session started (one GROUP BY)."""
    rows = conn.execute(
        "SELECT r.speaker_label AS label, "
        "CAST((r.wall - s.started_t) / 60 AS INTEGER) AS minute, "
        "round(sum(r.duration_s), 1) AS seconds "
        "FROM rows r JOIN sessions s ON s.session_id = r.session_id "
        "WHERE r.session_id = ? AND r.kind = 'caption' AND r.speaker_label IS NOT NULL "
        "GROUP BY label, minute ORDER BY label, minute",
        (session_id,),
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["label"], []).append(
            {"minute": max(0, row["minute"]), "seconds": row["seconds"]}
        )
    return out
