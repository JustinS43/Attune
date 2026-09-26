"""History database: connection, schema and the single writer thread (TODO H-10, H-11).

Python's built-in sqlite3 in WAL mode. All writes go through one writer thread fed by a
queue, so bus callbacks never block. If the database cannot be opened or written
(disk full, file locked, folder missing), rows wait in memory (bounded) and are written
once it works again; captions keep flowing regardless. Rows older than
``retention_hours`` are deleted at start and every ``purge_every_s`` seconds.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")

ROW_COLUMNS = (
    "session_id",
    "wall",
    "engine_t",
    "kind",
    "utt_id",
    "speaker_label",
    "speaker_kind",
    "person_id",
    "text",
    "translation",
    "lang",
    "duration_s",
    "alert_kind",
    "side",
)


def connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with the settings every user of the file needs."""
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False, timeout=2.0
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ------------------------------------------------------------------ write operations
# Each takes the writer connection plus plain arguments; run inside one transaction.


def op_begin_session(conn, session_id: str, started: float) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sessions(session_id, started_t) VALUES (?, ?)",
        (session_id, started),
    )


def op_end_session(conn, session_id: str, ended: float) -> None:
    conn.execute("UPDATE sessions SET ended_t = ? WHERE session_id = ?", (ended, session_id))


def op_insert_row(conn, row: dict) -> None:
    defaults = {"text": "", "duration_s": 0.0}
    values = [row.get(c) if row.get(c) is not None else defaults.get(c) for c in ROW_COLUMNS]
    conn.execute(
        f"INSERT INTO rows({', '.join(ROW_COLUMNS)}) VALUES ({', '.join('?' * len(ROW_COLUMNS))})",
        values,
    )


def op_upsert_caption(conn, row: dict) -> None:
    """A final caption re-published for the same utterance replaces the earlier text."""
    cur = conn.execute(
        "SELECT id FROM rows WHERE session_id = ? AND utt_id = ? AND kind = 'caption'",
        (row["session_id"], row["utt_id"]),
    ).fetchone()
    if cur is None:
        op_insert_row(conn, row)
        return
    conn.execute(
        "UPDATE rows SET text = ?, speaker_label = ?, speaker_kind = ?, person_id = ?, "
        "lang = ?, duration_s = ?, translation = coalesce(?, translation) WHERE id = ?",
        (
            row["text"],
            row["speaker_label"],
            row["speaker_kind"],
            row["person_id"],
            row["lang"],
            row["duration_s"],
            row.get("translation"),
            cur["id"],
        ),
    )


def op_delete_caption(conn, session_id: str, utt_id: str) -> None:
    """A caption saved from its draft whose segment was later retracted (A-22)."""
    conn.execute(
        "DELETE FROM rows WHERE session_id = ? AND utt_id = ? AND kind = 'caption'",
        (session_id, utt_id),
    )


def op_set_translation(conn, session_id: str, utt_id: str, text_en: str) -> None:
    conn.execute(
        "UPDATE rows SET translation = ? WHERE session_id = ? AND utt_id = ? AND kind = 'caption'",
        (text_en, session_id, utt_id),
    )


STRANGER = (
    "(speaker_kind IS NULL OR speaker_kind NOT IN ('you', 'you_typed')) "
    "AND (person_id IS NULL OR person_id LIKE 'session-%')"
)


def op_forget(conn, session_id: str, mode: str = "strangers") -> None:
    """Forget session: strangers' lines (default) or every row of the session."""
    if mode == "session":
        conn.execute("DELETE FROM rows WHERE session_id = ?", (session_id,))
        return
    conn.execute(
        f"DELETE FROM rows WHERE session_id = ? AND kind IN ('caption', 'translation') "
        f"AND {STRANGER}",
        (session_id,),
    )
    # names confirmed by touch are session-only
    conn.execute(
        "DELETE FROM rows WHERE session_id = ? AND kind = 'name_confirmed' "
        "AND (person_id IS NULL OR person_id LIKE 'session-%')",
        (session_id,),
    )


def op_purge(conn, cutoff: float, keep_session: str | None = None) -> int:
    """Delete rows (and then empty sessions) older than cutoff (epoch seconds)."""
    deleted = conn.execute("DELETE FROM rows WHERE wall < ?", (cutoff,)).rowcount
    conn.execute(
        "DELETE FROM sessions WHERE coalesce(ended_t, started_t) < ? AND session_id IS NOT ? "
        "AND NOT EXISTS (SELECT 1 FROM rows r WHERE r.session_id = sessions.session_id)",
        (cutoff, keep_session),
    )
    return deleted


class HistoryDB:
    """Owns the writer thread. ``submit`` never blocks and never raises."""

    def __init__(
        self,
        path: str | Path,
        retention_hours: float = 24,
        purge_every_s: float = 600,
        retry_s: float = 5.0,
        buffer_max: int = 20_000,
        wall: Callable[[], float] = time.time,
        keep_session: str | None = None,
    ):
        self.path = Path(path)
        self.retention_s = float(retention_hours) * 3600
        self.purge_every_s = float(purge_every_s)
        self.retry_s = float(retry_s)
        self.wall = wall
        self.keep_session = keep_session
        self.inbox: queue.SimpleQueue = queue.SimpleQueue()
        self.pending: deque = deque()
        self.buffer_max = int(buffer_max)
        self.conn: sqlite3.Connection | None = None
        self.error = ""
        self.metrics = {"written": 0, "dropped": 0, "purged": 0, "pending": 0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_purge = 0.0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="history-writer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop.set()
        self.inbox.put(None)
        if self._thread:
            self._thread.join(timeout=timeout)
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                logger.debug("ignored error", exc_info=True)
            self.conn = None

    def submit(self, fn: Callable, *args) -> None:
        self.inbox.put((fn, args))

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until everything submitted so far is written (tests, shutdown)."""
        done = threading.Event()
        self.inbox.put(("barrier", done))
        return done.wait(timeout)

    def purge_now(self) -> None:
        self.submit(self._purge_op)

    def _purge_op(self, conn) -> None:
        n = op_purge(conn, self.wall() - self.retention_s, self.keep_session)
        self.metrics["purged"] += n
        if n:
            logger.info("history: deleted %d rows older than %.0f h", n, self.retention_s / 3600)

    # ---------------------------------------------------------------- thread
    def _open(self) -> bool:
        if self.conn is not None:
            return True
        try:
            conn = connect(self.path)
            init_schema(conn)
            self.conn = conn
            self.error = ""
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = f"history database unavailable: {exc}"
            logger.warning("history: %s", self.error)
            return False

    def _run(self) -> None:
        barriers: list[threading.Event] = []
        while True:
            try:
                item = self.inbox.get(timeout=0.25)
            except queue.Empty:
                item = False
            stopping = item is None or self._stop.is_set()
            # gather everything that is waiting, keep order
            while item not in (False, None):
                if item[0] == "barrier":
                    barriers.append(item[1])
                else:
                    self.pending.append(item)
                try:
                    item = self.inbox.get_nowait()
                except queue.Empty:
                    item = False
                if item is None:
                    stopping = True
            if time.monotonic() >= self._next_purge:
                self._next_purge = time.monotonic() + self.purge_every_s
                self.pending.append((self._purge_op, ()))
            while len(self.pending) > self.buffer_max:
                self.pending.popleft()
                self.metrics["dropped"] += 1
            if self.pending and self._open():
                self._write_pending()
            self.metrics["pending"] = len(self.pending)
            if not self.pending:
                for event in barriers:
                    event.set()
                barriers.clear()
            if stopping:
                for event in barriers:
                    event.set()
                return
            if self.pending:  # database down: wait before retrying
                self._stop.wait(self.retry_s)

    def _write_pending(self) -> None:
        conn = self.conn
        while self.pending:
            fn, args = self.pending[0]
            try:
                with conn:
                    fn(conn, *args)
                self.pending.popleft()
                self.metrics["written"] += 1
            except sqlite3.OperationalError as exc:
                # disk full, locked, I/O error: keep the row and retry later
                self.error = f"history write failed: {exc}"
                logger.warning("history: %s (%d rows waiting)", self.error, len(self.pending))
                try:
                    conn.close()
                except Exception:
                    logger.debug("ignored error", exc_info=True)
                self.conn = None
                return
            except Exception:
                self.pending.popleft()
                self.metrics["dropped"] += 1
                logger.exception("history: row dropped")
        self.error = ""
