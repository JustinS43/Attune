"""HistoryService: saves every final caption, translation, reply, alert and confirmed name.

TODO H-11. One new session per engine start. Bus callbacks only build a row and hand it
to the writer thread (db.py), so a slow or broken database never delays captions.

Nothing said is lost (A-22): the newest draft of each caption is kept, and one whose final
never comes (the engine stopping mid-sentence, a final lost on the way) is written when it has
been quiet for ``history.orphan_draft_s`` and at stop. A later final replaces it; a retracted
segment is dropped.

``session.forget`` removes strangers' lines from the current session (the demo: "Sam's
lines are gone", enrolled people and your own lines stay). Set
``history.forget_mode = "session"`` to delete every row of the current session instead.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict

from attune.hardware.common import fields, section, shared_clock

from . import api
from . import db as dbm

logger = logging.getLogger(__name__)

ALERT_NAMES = {"smoke": "Smoke alarm", "co": "Carbon monoxide alarm", "doorbell": "Doorbell"}
SECONDS_PER_WORD = 0.4  # talk-time estimate when a caption has no word times


def _word_times(words) -> tuple[float | None, float | None]:
    """First start and last end from [(word, t0, t1)] or [{word, t0, t1}]."""
    starts, ends = [], []
    for w in words or []:
        try:
            if isinstance(w, dict):
                t0, t1 = w.get("t0", w.get("start")), w.get("t1", w.get("end"))
            else:
                t0, t1 = w[1], w[2]
            if t0 is not None and t1 is not None:
                starts.append(float(t0))
                ends.append(float(t1))
        except (TypeError, ValueError, IndexError):
            continue
    if not starts:
        return None, None
    return min(starts), max(ends)


class HistoryService:
    part = "history"

    def __init__(self, bus, config, *, wall=time.time):
        self.bus = bus
        self.config = config
        self.cfg = section(config, "history")
        self.wall = wall
        self.forget_mode = str(self.cfg.get("forget_mode", "strangers"))
        # A translation always fills its caption row's `translation`; set this to also add
        # a separate kind="translation" row (the history panel would then show it twice).
        self.translation_rows = bool(self.cfg.get("translation_rows", False))
        given = config.get("session_id") if isinstance(config, dict) else None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(wall()))
        self.session_id = str(given or f"{stamp}-{uuid.uuid4().hex[:4]}")
        self.db: dbm.HistoryDB | None = None
        self.utts: OrderedDict[str, dict] = OrderedDict()  # recent captions, for translations
        self.drafts: dict[str, tuple[dict, float]] = {}  # caption id -> newest draft row, when
        self.orphan_s = float(self.cfg.get("orphan_draft_s", 10.0))
        self.orphans: set[str] = set()  # drafts saved without a final (removed if retracted)
        self._lock = threading.RLock()  # drafts, orphans and utts (bus and status threads)
        self._unsubs: list = []
        self._stop = threading.Event()
        self._status_thread: threading.Thread | None = None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.clock = shared_clock(self.config)
        path = self.cfg.get("db_path", "data/history.db")
        self.db = dbm.HistoryDB(
            path,
            retention_hours=float(self.cfg.get("retention_hours", 24)),
            purge_every_s=float(self.cfg.get("purge_every_s", 600)),
            retry_s=float(self.cfg.get("retry_s", 5.0)),
            wall=self.wall,
            keep_session=self.session_id,
        )
        self.db.submit(dbm.op_begin_session, self.session_id, self.wall())
        self.db.start()  # opens the file, applies the schema, purges > 24 h
        api.bind(path, self.session_id)
        for topic, handler in (
            ("caption", self._on_caption),
            ("caption.retract", self._on_retract),
            ("caption.translation", self._on_translation),
            ("reply.spoken", self._on_reply),
            ("alert", self._on_alert),
            ("name.proposal", self._on_name),
            ("session.forget", self._on_forget),
        ):
            self._unsubs.append(self.bus.subscribe(topic, handler))
        self._stop.clear()
        self._status_thread = threading.Thread(
            target=self._status_loop, name="history-status", daemon=True
        )
        self._status_thread.start()

    def stop(self) -> None:
        self._stop.set()
        for unsub in self._unsubs:
            if callable(unsub):
                try:
                    unsub()
                except Exception:
                    logger.debug("ignored error", exc_info=True)
        self._unsubs.clear()
        if self.db:
            self._write_orphans(everything=True)  # words shown as a draft when we stopped
            self.db.submit(dbm.op_end_session, self.session_id, self.wall())
            self.db.flush(timeout=1.0)
            self.db.stop(timeout=0.5)
        if self._status_thread:
            self._status_thread.join(timeout=0.5)

    # ------------------------------------------------------------- helpers
    def _wall_at(self, engine_t) -> float:
        """Epoch seconds for an engine-clock time (now if missing or implausible)."""
        now = self.wall()
        try:
            delta = self.clock() - float(engine_t)
        except (TypeError, ValueError):
            return now
        return now - delta if -5.0 <= delta <= 3600.0 else now

    def _row(self, kind: str, **values) -> dict:
        row = {c: None for c in dbm.ROW_COLUMNS}
        row.update(session_id=self.session_id, kind=kind, text="", duration_s=0.0)
        row.update(values)
        return row

    # ------------------------------------------------------------- bus handlers
    def _on_caption(self, event) -> None:
        e = fields(event)
        text = str(e.get("text") or "").strip()
        final = bool(e.get("final"))
        if not final:
            if text and e.get("utt_id") is not None:
                with self._lock:
                    self.drafts[str(e["utt_id"])] = (self._caption_row(e, text), time.monotonic())
            return
        if e.get("utt_id") is not None:
            with self._lock:
                self.drafts.pop(str(e["utt_id"]), None)
                self.orphans.discard(str(e["utt_id"]))
        if not text:
            return
        self._save(self._caption_row(e, text))

    def _on_retract(self, event) -> None:
        utt = fields(event).get("utt_id")
        if utt is None:
            return
        with self._lock:
            self.drafts.pop(str(utt), None)
            saved = str(utt) in self.orphans
            self.orphans.discard(str(utt))
            if saved:
                self.utts.pop(str(utt), None)
        if saved:
            self.db.submit(dbm.op_delete_caption, self.session_id, str(utt))

    def _write_orphans(self, everything: bool = False) -> None:
        """Save drafts whose final never came (quiet for orphan_draft_s, or all at stop)."""
        now = time.monotonic()
        with self._lock:
            old = [k for k, (_, t) in self.drafts.items() if everything or now - t >= self.orphan_s]
            rows = [self.drafts.pop(k)[0] for k in old]
            self.orphans.update(old)
            while len(self.orphans) > 500:
                self.orphans.pop()
        for row in rows:
            self._save(row)
        if rows:
            logger.info("history: saved %d caption(s) whose final never came", len(rows))

    def _save(self, row: dict) -> None:
        with self._lock:
            self.utts[row["utt_id"]] = row
            while len(self.utts) > 500:
                self.utts.popitem(last=False)
        self.db.submit(dbm.op_upsert_caption, row)

    def _caption_row(self, e: dict, text: str) -> dict:
        speaker = fields(e.get("speaker"))
        t0, t1 = _word_times(e.get("words"))
        engine_t = t0 if t0 is not None else self.clock()
        duration = (t1 - t0) if t0 is not None else len(text.split()) * SECONDS_PER_WORD
        utt_id = str(e.get("utt_id") or uuid.uuid4().hex)
        return self._row(
            "caption",
            wall=self._wall_at(engine_t),
            engine_t=engine_t,
            utt_id=utt_id,
            speaker_label=speaker.get("label") or "Someone",
            speaker_kind=speaker.get("kind"),
            person_id=speaker.get("person_id"),
            text=text,
            translation=e.get("translation"),
            lang=e.get("lang"),
            duration_s=round(max(0.0, min(duration, 120.0)), 2),
        )

    def _on_translation(self, event) -> None:
        e = fields(event)
        utt_id, text_en = e.get("utt_id"), str(e.get("text_en") or "").strip()
        caption = self.utts.get(str(utt_id)) if utt_id is not None else None
        if caption is None or not text_en:
            return  # caption unknown (or forgotten): nothing to attach it to
        caption["translation"] = text_en
        self.db.submit(dbm.op_set_translation, self.session_id, str(utt_id), text_en)
        if not self.translation_rows:
            return  # shown inline on the caption row (its `translation` field)
        self.db.submit(
            dbm.op_insert_row,
            self._row(
                "translation",
                wall=self.wall(),
                engine_t=self.clock(),
                utt_id=str(utt_id),
                speaker_label=caption["speaker_label"],
                speaker_kind=caption["speaker_kind"],
                person_id=caption["person_id"],
                text=text_en,
                translation=text_en,
                lang="en",
            ),
        )

    def _on_reply(self, event) -> None:
        e = fields(event)
        text = str(e.get("text") or "").strip()
        if not text:
            return
        engine_t = e.get("t", self.clock())
        self.db.submit(
            dbm.op_insert_row,
            self._row(
                "reply",
                wall=self._wall_at(engine_t),
                engine_t=engine_t,
                speaker_label="You (typed)",
                speaker_kind="you_typed",
                text=text,
                duration_s=0.0,
            ),
        )

    def _on_alert(self, event) -> None:
        e = fields(event)
        if e.get("state") != "start":
            return
        kind, side = e.get("kind"), e.get("side") or "none"
        name = ALERT_NAMES.get(kind, str(kind or "Alert").capitalize())
        text = name if side in ("none", None) else f"{name}, {side}"
        self.db.submit(
            dbm.op_insert_row,
            self._row(
                "alert",
                wall=self.wall(),
                engine_t=self.clock(),
                utt_id=e.get("alert_id"),
                text=text,
                alert_kind=kind,
                side=side,
            ),
        )

    def _on_name(self, event) -> None:
        e = fields(event)
        if e.get("state") != "confirmed" or not e.get("name"):
            return
        self.db.submit(
            dbm.op_insert_row,
            self._row(
                "name_confirmed",
                wall=self.wall(),
                engine_t=self.clock(),
                utt_id=e.get("proposal_id"),
                speaker_label=str(e["name"]),
                person_id=e.get("person_id"),
                text=f"Name confirmed: {e['name']}",
            ),
        )

    def _on_forget(self, _event=None) -> None:
        def stranger(r: dict) -> bool:
            return r["speaker_kind"] not in ("you", "you_typed") and (
                r["person_id"] is None or str(r["person_id"]).startswith("session-")
            )

        with self._lock:
            everyone = self.forget_mode == "session"
            for key in [k for k, (r, _) in self.drafts.items() if everyone or stranger(r)]:
                self.drafts.pop(key)
            for key in [k for k, r in self.utts.items() if everyone or stranger(r)]:
                self.utts.pop(key)
                self.orphans.discard(key)
        self.db.submit(dbm.op_forget, self.session_id, self.forget_mode)

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        db = self.db
        error = db.error if db else "not started"
        metrics = dict(db.metrics) if db else {}
        metrics["session_id"] = self.session_id
        detail = error or f"saving to {db.path}" if db else "not started"
        return {"part": self.part, "ok": not error, "detail": detail, "metrics": metrics}

    def _status_loop(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self._write_orphans()
            except Exception:
                logger.exception("history: saving unfinished captions failed")
            try:
                self.bus.publish("status.part", self.status())
            except Exception:
                logger.exception("history: status publish failed")
