"""Content-addressed cache for every model output.

The key is a SHA-256 of the exact request (namespace + canonical JSON of every input that can
change the output), so a rerun with the same inputs makes zero network calls. JSON results live
in SQLite; audio blobs live in files named by their own hash.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Any


def stable_hash(obj: Any) -> str:
    data = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def bytes_hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "blobs").mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.root / "cache.sqlite", check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, ns TEXT, v TEXT)")
        self._db.commit()
        self.hits: Counter[str] = Counter()
        self.misses: Counter[str] = Counter()

    def key(self, ns: str, request: Any) -> str:
        return f"{ns}:{stable_hash(request)}"

    def get(self, ns: str, request: Any) -> Any | None:
        k = self.key(ns, request)
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        if row is None:
            self.misses[ns] += 1
            return None
        self.hits[ns] += 1
        return json.loads(row[0])

    def set(self, ns: str, request: Any, value: Any) -> None:
        k = self.key(ns, request)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO kv (k, ns, v) VALUES (?,?,?)", (k, ns, json.dumps(value, ensure_ascii=False)))
            self._db.commit()

    def put_blob(self, data: bytes, suffix: str = ".wav") -> str:
        h = bytes_hash(data)
        p = self.root / "blobs" / f"{h}{suffix}"
        if not p.exists():
            p.write_bytes(data)
        return p.name

    def blob_path(self, name: str) -> Path:
        return self.root / "blobs" / name

    def stats(self) -> dict:
        return {"hits": dict(sorted(self.hits.items())), "misses": dict(sorted(self.misses.items()))}

    def close(self) -> None:
        with self._lock:
            self._db.close()
