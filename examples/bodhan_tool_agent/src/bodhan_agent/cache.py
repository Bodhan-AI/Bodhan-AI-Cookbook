"""
Run-scoped SQLite cache for Bodhan model outputs.

Key = sha256(model_id + sorted-JSON of inputs). Stored and retrieved as JSON.
One cache database lives in the --out directory, so each run starts fresh.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


class ResultCache:
    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.commit()

    @staticmethod
    def _key(model: str, inputs: dict) -> str:
        raw = json.dumps({"_model": model, **inputs}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, model: str, inputs: dict) -> dict | None:
        row = self._conn.execute(
            "SELECT value FROM cache WHERE key=?", (self._key(model, inputs),)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, model: str, inputs: dict, value: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)",
            (self._key(model, inputs), json.dumps(value)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
