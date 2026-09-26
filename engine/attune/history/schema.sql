-- Conversation history (TODO H-10). SQLite, file data/history.db (gitignored).
--
-- Text only. Never audio, video, face prints or voice prints.
-- Times: `wall` is Unix epoch seconds (for the 24 h retention and for display);
-- `engine_t` is the engine's shared clock at the time (only meaningful inside a session).
-- Rows older than history.retention_hours (24) are deleted by db.py at start and every
-- 10 minutes; FTS rows follow through the triggers below.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    started_t   REAL NOT NULL,          -- epoch seconds
    ended_t     REAL                    -- epoch seconds; NULL while running
);

CREATE TABLE IF NOT EXISTS rows (
    id             INTEGER PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    wall           REAL NOT NULL,       -- epoch seconds when it was said / happened
    engine_t       REAL,                -- shared engine clock
    kind           TEXT NOT NULL CHECK (kind IN
                       ('caption', 'translation', 'reply', 'alert', 'name_confirmed')),
    utt_id         TEXT,                -- links a translation to its caption
    speaker_label  TEXT,
    speaker_kind   TEXT,                -- you, you_typed, face, probable_face, offscreen, someone
    person_id      TEXT,                -- enrolled id, 'session-...' or NULL (stranger)
    text           TEXT NOT NULL DEFAULT '',
    translation    TEXT,
    lang           TEXT,
    duration_s     REAL NOT NULL DEFAULT 0,   -- speaking time, for the talk-time chart
    alert_kind     TEXT,                -- smoke, co, doorbell
    side           TEXT                 -- left, right, none
);

CREATE INDEX IF NOT EXISTS rows_session_time ON rows(session_id, wall);
CREATE INDEX IF NOT EXISTS rows_wall ON rows(wall);
CREATE INDEX IF NOT EXISTS rows_utt ON rows(utt_id);
CREATE INDEX IF NOT EXISTS rows_speaker ON rows(speaker_label COLLATE NOCASE);

-- Full-text search over what was said, its translation and who said it.
CREATE VIRTUAL TABLE IF NOT EXISTS rows_fts USING fts5(
    text, translation, speaker_label,
    content = 'rows', content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS rows_ai AFTER INSERT ON rows BEGIN
    INSERT INTO rows_fts(rowid, text, translation, speaker_label)
    VALUES (new.id, new.text, coalesce(new.translation, ''), coalesce(new.speaker_label, ''));
END;

CREATE TRIGGER IF NOT EXISTS rows_ad AFTER DELETE ON rows BEGIN
    INSERT INTO rows_fts(rows_fts, rowid, text, translation, speaker_label)
    VALUES ('delete', old.id, old.text, coalesce(old.translation, ''),
            coalesce(old.speaker_label, ''));
END;

CREATE TRIGGER IF NOT EXISTS rows_au AFTER UPDATE ON rows BEGIN
    INSERT INTO rows_fts(rows_fts, rowid, text, translation, speaker_label)
    VALUES ('delete', old.id, old.text, coalesce(old.translation, ''),
            coalesce(old.speaker_label, ''));
    INSERT INTO rows_fts(rowid, text, translation, speaker_label)
    VALUES (new.id, new.text, coalesce(new.translation, ''), coalesce(new.speaker_label, ''));
END;
