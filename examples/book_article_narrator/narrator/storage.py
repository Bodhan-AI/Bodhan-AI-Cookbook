"""Keeps recent narrations in a local SQLite file so a user can replay them.

Only this file talks to the database. Every query uses ? placeholders, so pasted
text can never be run as SQL. The newest HISTORY_LIMIT narrations are kept.
"""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS narrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    language TEXT NOT NULL,
    voice TEXT NOT NULL,
    style TEXT,
    original_text TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    audio BLOB NOT NULL
)
"""

LIST_COLUMNS = "id, created_at, language, voice, style, original_text, translated_text"


def _connect(db_path):
    connection = sqlite3.connect(db_path or config.DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(SCHEMA)
    return connection


def save_narration(result, db_path=None, limit=config.HISTORY_LIMIT):
    """Store a finished NarrationResult and return its id."""
    with closing(_connect(db_path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO narrations (created_at, language, voice, style, "
            "original_text, translated_text, audio) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                result.target_language,
                result.voice,
                result.style,
                result.original_text,
                "\n\n".join(result.translated_paragraphs),
                result.wav_bytes,
            ),
        )
        connection.execute(
            "DELETE FROM narrations WHERE id NOT IN "
            "(SELECT id FROM narrations ORDER BY id DESC LIMIT ?)",
            (limit,),
        )
        return cursor.lastrowid


def list_narrations(db_path=None):
    """Newest first, without the audio (which is large)."""
    with closing(_connect(db_path)) as connection:
        rows = connection.execute(
            f"SELECT {LIST_COLUMNS} FROM narrations ORDER BY id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def load_narration(narration_id, db_path=None):
    """One full narration including audio, or None if it does not exist."""
    with closing(_connect(db_path)) as connection:
        row = connection.execute(
            "SELECT * FROM narrations WHERE id = ?", (narration_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_narration(narration_id, db_path=None):
    with closing(_connect(db_path)) as connection, connection:
        connection.execute("DELETE FROM narrations WHERE id = ?", (narration_id,))


def clear_history(db_path=None):
    """Delete every saved narration."""
    with closing(_connect(db_path)) as connection, connection:
        connection.execute("DELETE FROM narrations")