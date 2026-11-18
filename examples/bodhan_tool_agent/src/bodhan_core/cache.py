"""Content-addressed SQLite cache for Bodhan responses.

The key is a hash of (model, request params, input bytes), so the same input is never sent to the
same model twice, within a run or across reruns. Verification retries that must re-call a model
pass a distinct ``attempt`` param, so they are explicit in the key and in the trace.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path


def cache_key(model: str, params: dict, payload: bytes = b"") -> str:
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(b"\0")
    h.update(json.dumps(params, sort_keys=True, ensure_ascii=False, default=str).encode())
    h.update(b"\0")
    h.update(hashlib.sha256(payload).digest())
    return h.hexdigest()


class Cache:
    def __init__(self, path: Path | str | None):
        """``path=None`` disables caching (every lookup misses)."""
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS responses ("
                    " key TEXT PRIMARY KEY, model TEXT, data TEXT, content BLOB, created REAL)"
                )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def get(self, key: str) -> tuple[dict | None, bytes | None] | None:
        if not self.path:
            return None
        with self._lock, self._connect() as db:
            row = db.execute("SELECT data, content FROM responses WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        data, content = row
        return (json.loads(data) if data is not None else None), content

    def put(self, key: str, model: str, data: dict | None, content: bytes | None) -> None:
        if not self.path:
            return
        blob = json.dumps(data, ensure_ascii=False) if data is not None else None
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO responses (key, model, data, content, created) VALUES (?, ?, ?, ?, ?)",
                (key, model, blob, content, time.time()),
            )
