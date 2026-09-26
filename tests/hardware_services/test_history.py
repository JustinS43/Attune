"""History service, queries and API against a temporary SQLite file."""

import sqlite3
import time

import pytest
from attune.history import api, queries
from attune.history import db as dbm
from attune.history.service import HistoryService
from fastapi import FastAPI
from fastapi.testclient import TestClient


class Clocks:
    """Engine clock and wall clock that move together."""

    def __init__(self):
        self.engine = 100.0
        self.wall0 = 1_790_000_000.0

    def now(self):
        return self.engine

    def wall(self):
        return self.wall0 + self.engine


@pytest.fixture
def clocks():
    return Clocks()


@pytest.fixture
def history(bus, tmp_path, clocks):
    services = []

    def build(**cfg):
        config = {
            "clock": clocks.now,
            "history": {"db_path": str(tmp_path / "history.db"), **cfg},
        }
        service = HistoryService(bus, config, wall=clocks.wall)
        service.start()
        services.append(service)
        return service

    yield build
    for service in services:
        service.stop()


def caption(
    bus,
    utt,
    text,
    label,
    kind="face",
    person=None,
    t0=100.0,
    t1=101.5,
    lang="en",
    final=True,
):
    bus.publish(
        "caption",
        {
            "utt_id": utt,
            "speaker": {
                "kind": kind,
                "track_id": 1,
                "person_id": person,
                "label": label,
                "side": "none",
            },
            "text": text,
            "final": final,
            "lang": lang,
            "words": [
                (w, t0 + i * 0.3, min(t1, t0 + i * 0.3 + 0.3))
                for i, w in enumerate(text.split())
            ]
            if t1
            else [],
        },
    )


def read(service):
    service.db.flush()
    conn = dbm.connect(service.db.path, readonly=True)
    try:
        return queries.timeline(conn, service.session_id)
    finally:
        conn.close()


def test_saves_final_rows_only(bus, history):
    service = history()
    caption(bus, "u1", "So what are we", "Maya", person="p-maya", final=False)
    caption(bus, "u1", "So what are we building?", "Maya", person="p-maya")
    bus.publish(
        "caption.translation", {"utt_id": "u1", "source_lang": "en", "text_en": "x"}
    )
    bus.publish(
        "reply.spoken", {"text": "Nice to meet you", "voice": "kokoro", "t": 100.5}
    )
    bus.publish(
        "alert",
        {
            "alert_id": "a1",
            "kind": "smoke",
            "side": "left",
            "confidence": 0.9,
            "state": "start",
        },
    )
    bus.publish(
        "alert",
        {
            "alert_id": "a1",
            "kind": "smoke",
            "side": "left",
            "confidence": 0.9,
            "state": "update",
        },
    )
    bus.publish(
        "name.proposal",
        {
            "proposal_id": "n1",
            "track_id": 2,
            "name": "Sam",
            "state": "proposed",
            "expires_t": 110,
        },
    )
    bus.publish(
        "name.proposal",
        {
            "proposal_id": "n1",
            "track_id": 2,
            "name": "Sam",
            "state": "confirmed",
            "expires_t": 110,
        },
    )
    rows = read(service)
    assert [r["kind"] for r in rows].count("caption") == 1
    kinds = sorted(r["kind"] for r in rows)
    assert kinds == ["alert", "caption", "name_confirmed", "reply"]
    cap = next(r for r in rows if r["kind"] == "caption")
    assert cap["speaker_label"] == "Maya" and cap["text"] == "So what are we building?"
    assert cap["translation"] == "x"
    reply = next(r for r in rows if r["kind"] == "reply")
    assert reply["speaker_label"] == "You (typed)"
    alert = next(r for r in rows if r["kind"] == "alert")
    assert alert["text"] == "Smoke alarm, left" and alert["side"] == "left"
    for r in rows:
        assert set(r) >= {"t", "kind", "speaker_label", "text", "translation", "lang"}


def test_translation_for_unknown_caption_is_ignored(bus, history):
    service = history()
    bus.publish(
        "caption.translation", {"utt_id": "nope", "source_lang": "es", "text_en": "hi"}
    )
    assert read(service) == []


def test_repeated_final_updates_same_row(bus, history):
    service = history()
    caption(bus, "u1", "hola que tal", "Leo", lang="es")
    caption(bus, "u1", "Hola, ¿qué tal?", "Leo", lang="es")
    rows = read(service)
    assert len(rows) == 1 and rows[0]["text"] == "Hola, ¿qué tal?"


def test_fts_search_and_person_filter(bus, history):
    service = history()
    caption(bus, "u1", "So what are we building?", "Maya", person="p-maya")
    caption(bus, "u2", "A prototype for captions", "Leo", person="p-leo")
    caption(bus, "u3", "Café con leche por favor", "Person in blue jacket", lang="es")
    service.db.flush()
    conn = dbm.connect(service.db.path, readonly=True)
    try:
        assert [r["utt_id"] for r in queries.search(conn, "build")] == ["u1"]
        assert [r["utt_id"] for r in queries.search(conn, "CAPTIONS prototype")] == [
            "u2"
        ]
        assert [r["utt_id"] for r in queries.search(conn, "cafe")] == [
            "u3"
        ]  # diacritics
        assert [r["utt_id"] for r in queries.search(conn, "", person="leo")] == ["u2"]
        assert queries.search(conn, "build", person="Leo") == []
        assert queries.search(conn, '"; DROP TABLE rows; --') == []
        assert queries.search(conn, "!!!") == []
        t0 = time.perf_counter()
        queries.search(conn, "captions")
        assert time.perf_counter() - t0 < 0.5
    finally:
        conn.close()


def test_talktime_by_person_and_minute(bus, history, clocks):
    service = history()
    caption(bus, "u1", "one two three four five", "Maya", t0=100.0, t1=101.5)
    caption(bus, "u2", "six seven", "Leo", t0=102.0, t1=102.6)
    clocks.engine = 170.0
    caption(bus, "u3", "later words here", "Maya", t0=170.0, t1=171.0)
    service.db.flush()
    conn = dbm.connect(service.db.path, readonly=True)
    try:
        tt = queries.talktime(conn, service.session_id)
    finally:
        conn.close()
    assert tt["Maya"] == [
        {"minute": 0, "seconds": 1.5},
        {"minute": 1, "seconds": 0.9},
    ]  # 3 words x 0.3 s
    assert tt["Leo"] == [{"minute": 0, "seconds": 0.6}]


def test_old_rows_deleted_after_24h(bus, history, tmp_path, clocks):
    path = tmp_path / "history.db"
    conn = dbm.connect(path)
    dbm.init_schema(conn)
    old = clocks.wall() - 25 * 3600
    with conn:
        dbm.op_begin_session(conn, "old", old)
        dbm.op_end_session(conn, "old", old + 60)
        dbm.op_insert_row(
            conn,
            {
                "session_id": "old",
                "wall": old,
                "kind": "caption",
                "text": "ancient words",
                "speaker_label": "Maya",
            },
        )
        dbm.op_begin_session(conn, "recent", clocks.wall() - 3600)
        dbm.op_insert_row(
            conn,
            {
                "session_id": "recent",
                "wall": clocks.wall() - 3600,
                "kind": "caption",
                "text": "recent words",
                "speaker_label": "Maya",
            },
        )
    conn.close()
    service = history()  # start-up purge
    service.db.flush()
    conn = dbm.connect(path, readonly=True)
    try:
        sessions = [s["session_id"] for s in queries.sessions(conn)]
        assert "old" not in sessions and "recent" in sessions
        assert queries.search(conn, "ancient") == []
        assert [r["text"] for r in queries.search(conn, "recent")] == ["recent words"]
        # the FTS index follows the deletes
        n = conn.execute("SELECT count(*) FROM rows_fts WHERE rows_fts MATCH 'ancient'")
        assert n.fetchone()[0] == 0
    finally:
        conn.close()


def test_forget_removes_strangers_only(bus, history):
    service = history()
    caption(bus, "u1", "Hi I'm Sam", "Sam", person="session-abc123")
    caption(bus, "u2", "Hello there", "Person in blue jacket", person=None)
    caption(bus, "u3", "Hey Sam", "Maya", person="p-maya")
    caption(bus, "u4", "Nice", "You", kind="you")
    bus.publish(
        "caption.translation", {"utt_id": "u2", "source_lang": "es", "text_en": "hi"}
    )
    bus.publish(
        "name.proposal", {"proposal_id": "n1", "name": "Sam", "state": "confirmed"}
    )
    bus.publish(
        "reply.spoken", {"text": "Nice to meet you", "voice": "kokoro", "t": 100}
    )
    bus.publish("session.forget", {})
    bus.publish(
        "caption.translation", {"utt_id": "u1", "source_lang": "es", "text_en": "late"}
    )
    rows = read(service)
    assert sorted(r["utt_id"] or r["kind"] for r in rows) == ["reply", "u3", "u4"]


def test_forget_session_mode_deletes_everything(bus, history):
    service = history(forget_mode="session")
    caption(bus, "u1", "Hey", "Maya", person="p-maya")
    bus.publish("session.forget", {})
    assert read(service) == []


def test_database_failure_buffers_rows(bus, tmp_path, clocks):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    config = {
        "clock": clocks.now,
        "history": {"db_path": str(blocker / "history.db"), "retry_s": 0.1},
    }
    service = HistoryService(bus, config, wall=clocks.wall)
    service.start()
    try:
        caption(bus, "u1", "still captioning", "Maya")  # must not raise
        time.sleep(0.3)
        assert service.db.error
        assert service.db.metrics["pending"] >= 2  # session + caption waiting in memory
        # the disk comes back
        good = tmp_path / "ok" / "history.db"
        service.db.path = good
        assert service.db.flush(timeout=3.0)
        conn = dbm.connect(good, readonly=True)
        assert [r["text"] for r in queries.timeline(conn, service.session_id)] == [
            "still captioning"
        ]
        conn.close()
    finally:
        service.stop()


def test_never_stores_audio(bus, history):
    service = history()
    service.db.flush()
    conn = sqlite3.connect(service.db.path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(rows)")}
    conn.close()
    assert not cols & {"audio", "samples", "image", "jpeg", "embedding", "print"}


# ------------------------------------------------------------------ API


@pytest.fixture
def client(bus, history):
    service = history()
    caption(bus, "u1", "So what are we building?", "Maya", person="p-maya")
    caption(bus, "u2", "Captions for everyone", "Leo", person="p-leo")
    bus.publish(
        "alert",
        {
            "alert_id": "a1",
            "kind": "doorbell",
            "side": "right",
            "confidence": 0.8,
            "state": "start",
        },
    )
    service.db.flush()
    app = FastAPI()
    app.include_router(api.router, prefix="/api/history")
    return TestClient(app), service


def test_api_sessions_and_timeline(client):
    c, service = client
    sessions = c.get("/api/history/sessions").json()
    assert sessions[0]["session_id"] == service.session_id
    assert sessions[0]["lines"] == 3
    assert set(sessions[0]) == {"session_id", "started_t", "ended_t", "lines"}
    rows = c.get(f"/api/history/sessions/{service.session_id}").json()
    assert [r["kind"] for r in rows] == ["caption", "caption", "alert"]
    assert c.get("/api/history/sessions/current").json() == rows
    assert c.get("/api/history/sessions/unknown").json() == []


def test_api_search_missed_talktime(client):
    c, service = client
    assert [
        r["utt_id"]
        for r in c.get("/api/history/search", params={"q": "caption"}).json()
    ] == ["u2"]
    assert [
        r["utt_id"]
        for r in c.get("/api/history/search", params={"person": "Maya"}).json()
    ] == ["u1"]
    missed = c.get("/api/history/missed", params={"session": service.session_id}).json()
    assert [(m["kind"], m["alert_kind"], m["side"]) for m in missed] == [
        ("alert", "doorbell", "right")
    ]
    tt = c.get("/api/history/talktime").json()
    assert set(tt) == {"Maya", "Leo"} and tt["Maya"][0]["minute"] == 0


def test_api_without_database(tmp_path):
    api.bind(tmp_path / "missing.db")
    app = FastAPI()
    app.include_router(api.router, prefix="/api/history")
    assert TestClient(app).get("/api/history/sessions").status_code == 503


def test_translation_rows_optional(bus, history):
    service = history(translation_rows=True)
    caption(bus, "u1", "Hola", "Leo", lang="es")
    bus.publish(
        "caption.translation", {"utt_id": "u1", "source_lang": "es", "text_en": "Hi"}
    )
    rows = read(service)
    assert [(r["kind"], r["text"], r["translation"]) for r in rows] == [
        ("caption", "Hola", "Hi"),
        ("translation", "Hi", "Hi"),
    ]
