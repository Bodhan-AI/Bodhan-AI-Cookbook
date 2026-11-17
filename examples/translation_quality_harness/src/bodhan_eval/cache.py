"""Content-addressed cache for every model output, keyed by the exact request.

JSON results live in one SQLite file; audio bytes live next to it as ``<key>.wav`` so the
database stays small. A rerun with a warm cache makes no model calls and costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path


def cache_key(namespace: str, payload: dict) -> str:
    blob = json.dumps({"ns": namespace, **payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.root / "cache.sqlite", check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._db.commit()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))
            self._db.commit()

    def blob_path(self, key: str) -> Path:
        return self.root / "audio" / f"{key}.wav"

    def put_blob(self, key: str, data: bytes) -> Path:
        path = self.blob_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path
