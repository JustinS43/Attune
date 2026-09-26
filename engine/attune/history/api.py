"""FastAPI router for the history panel (TODO H-12). Section 4 mounts it:

    from attune.history.api import router as history_router
    app.include_router(history_router, prefix="/api/history")

Routes (docs/contracts.md section 5): ``/sessions``, ``/sessions/{id}``,
``/search?q=&person=``, ``/missed?session=``, ``/talktime?session=``. A session id of
``current`` (or leaving ``session`` out) means the running session, else the newest one.
HistoryService.start() calls :func:`bind`; before that the default path is used.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from . import queries
from .db import connect

router = APIRouter(tags=["history"])

_bound: dict = {"path": Path("data/history.db"), "session_id": None}


def bind(db_path: str | Path, session_id: str | None = None) -> None:
    """Point the router at the database the HistoryService writes."""
    _bound["path"] = Path(db_path)
    _bound["session_id"] = session_id


def get_conn() -> Iterator[sqlite3.Connection]:
    path = _bound["path"]
    if not Path(path).exists():
        raise HTTPException(status_code=503, detail="history database not created yet")
    try:
        conn = connect(path, readonly=True)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="history database unavailable") from exc
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _session(conn: sqlite3.Connection, session_id: str | None) -> str | None:
    if session_id in (None, "", "current", "latest"):
        current = _bound["session_id"] if session_id != "latest" else None
        return current or queries.latest_session(conn)
    return session_id


@router.get("/sessions")
def list_sessions(conn: Conn) -> list[dict]:
    return queries.sessions(conn)


@router.get("/sessions/{session_id}")
def session_timeline(session_id: str, conn: Conn) -> list[dict]:
    sid = _session(conn, session_id)
    if sid is None:
        return []
    return queries.timeline(conn, sid)


@router.get("/search")
def search(
    conn: Conn,
    q: str = "",
    person: str | None = None,
    session: str | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 200,
) -> list[dict]:
    sid = _session(conn, session) if session else None
    return queries.search(conn, q, person or None, sid, limit)


@router.get("/missed")
def missed(conn: Conn, session: str | None = None) -> list[dict]:
    sid = _session(conn, session)
    return queries.missed(conn, sid) if sid else []


@router.get("/talktime")
def talktime(conn: Conn, session: str | None = None) -> dict[str, list[dict]]:
    sid = _session(conn, session)
    return queries.talktime(conn, sid) if sid else {}
