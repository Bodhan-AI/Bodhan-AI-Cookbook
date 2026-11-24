"""Content-addressed cache of every model output, keyed by its exact input.

Key = sha256 of canonical JSON {namespace, payload}. The payload must contain everything
that can change the output (model id, endpoint, all params, input text / audio hash).
A rerun with a warm cache therefore makes zero paid calls and reproduces the same report.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Cache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            " key TEXT PRIMARY KEY, namespace TEXT NOT NULL, request TEXT NOT NULL,"
            " value BLOB NOT NULL, meta TEXT NOT NULL, created REAL NOT NULL)"
        )
        self._db.commit()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(namespace: str, payload: Any) -> str:
        return hashlib.sha256(canonical_json({"ns": namespace, "p": payload}).encode()).hexdigest()

    def get(self, namespace: str, payload: Any) -> tuple[bytes, dict] | None:
        k = self.key(namespace, payload)
        with self._lock:
            row = self._db.execute("SELECT value, meta FROM entries WHERE key=?", (k,)).fetchone()
            if row is None:
                self.misses += 1
                return None
            self.hits += 1
        return bytes(row[0]), json.loads(row[1])

    def put(self, namespace: str, payload: Any, value: bytes, meta: dict | None = None) -> None:
        k = self.key(namespace, payload)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?)",
                (
                    k,
                    namespace,
                    canonical_json(payload),
                    value,
                    canonical_json(meta or {}),
                    time.time(),
                ),
            )
            self._db.commit()

    def get_json(self, namespace: str, payload: Any) -> tuple[Any, dict] | None:
        hit = self.get(namespace, payload)
        return (json.loads(hit[0]), hit[1]) if hit else None

    def put_json(self, namespace: str, payload: Any, value: Any, meta: dict | None = None) -> None:
        self.put(namespace, payload, canonical_json(value).encode(), meta)

    def close(self) -> None:
        with self._lock:
            self._db.close()
