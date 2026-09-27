"""A-22: a caption whose final never comes is still saved (quiet draft, or engine stop)."""

import pytest
from attune.history import db as dbm
from attune.history import queries
from attune.history.service import HistoryService


@pytest.fixture
def make(bus, tmp_path):
    services = []

    def build(**cfg):
        config = {
            "clock": lambda: 100.0,
            "history": {"db_path": str(tmp_path / "history.db"), **cfg},
        }
        service = HistoryService(bus, config, wall=lambda: 1_790_000_100.0)
        service.start()
        services.append(service)
        return service

    yield build
    for service in services:
        service.stop()


def caption(bus, utt, text, final=False, kind="someone", person=None):
    bus.publish(
        "caption",
        {
            "utt_id": utt,
            "speaker": {
                "kind": kind,
                "track_id": None,
                "person_id": person,
                "label": "Someone",
            },
            "text": text,
            "final": final,
            "lang": "en",
            "words": [
                (w, 100.0 + i * 0.3, 100.3 + i * 0.3)
                for i, w in enumerate(text.split())
            ],
        },
    )


def texts(path, session_id):
    conn = dbm.connect(path, readonly=True)
    try:
        return [
            r["text"]
            for r in queries.timeline(conn, session_id)
            if r["kind"] == "caption"
        ]
    finally:
        conn.close()


def test_a_quiet_draft_is_saved_and_its_late_final_replaces_it(bus, make):
    service = make(orphan_draft_s=0.0)
    caption(bus, "u1", "So what are")
    caption(bus, "u1", "So what are we building")
    service._write_orphans()
    service.db.flush()
    assert texts(service.db.path, service.session_id) == ["So what are we building"]
    caption(bus, "u1", "So what are we building?", final=True)
    service.db.flush()
    assert texts(service.db.path, service.session_id) == ["So what are we building?"]


def test_drafts_still_changing_or_finished_are_not_saved_early(bus, make):
    service = make(orphan_draft_s=60.0)
    caption(bus, "u1", "Hello there")
    caption(bus, "u2", "See you")
    caption(bus, "u2", "See you soon.", final=True)
    service._write_orphans()
    service.db.flush()
    assert texts(service.db.path, service.session_id) == ["See you soon."]
    assert list(service.drafts) == ["u1"]


def test_stopping_mid_sentence_saves_the_words_on_screen(bus, make, tmp_path):
    service = make(orphan_draft_s=60.0)
    caption(bus, "u1", "Let me tell you about")
    path, session = service.db.path, service.session_id
    service.stop()
    assert texts(path, session) == ["Let me tell you about"]


def test_a_retracted_segment_is_never_saved_or_is_removed(bus, make):
    service = make(orphan_draft_s=0.0)
    caption(bus, "u1.1", "about the")
    bus.publish("caption.retract", {"utt_id": "u1.1"})
    service._write_orphans()
    service.db.flush()
    assert texts(service.db.path, service.session_id) == []
    caption(bus, "u2.1", "wrong speaker part")
    service._write_orphans()
    bus.publish("caption.retract", {"utt_id": "u2.1"})
    service.db.flush()
    assert texts(service.db.path, service.session_id) == []


def test_forget_drops_a_strangers_unsaved_draft(bus, make):
    service = make(orphan_draft_s=0.0)
    caption(bus, "u1", "my address is", kind="face", person=None)
    caption(bus, "u2", "I'll call you", kind="you")
    bus.publish("session.forget", {})
    service._write_orphans()
    service.db.flush()
    assert texts(service.db.path, service.session_id) == ["I'll call you"]
