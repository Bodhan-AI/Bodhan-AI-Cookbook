"""Content-addressed cache: SQLite for JSON responses, files for audio blobs.

Keys are SHA-256 over a canonical JSON of (namespace, model, payload, params). Each entry
also stores the INR cost of producing it, so a fully cached rerun can still report what the
outputs cost while spending nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def make_key(namespace: str, model: str, payload: Any, params: Any = None) -> str:
    blob = canonical_json({"ns": namespace, "model": model, "payload": payload, "params": params or {}})
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


class Cache:
    def __init__(self, root: str | Path, *, enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.root / "cache.sqlite", check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, ns TEXT, value TEXT, cost_inr REAL)")

    def get(self, key: str) -> tuple[Any, float] | None:
        if not self.enabled:
            return None
        with self._lock:
            row = self._db.execute("SELECT value, cost_inr FROM entries WHERE key = ?", (key,)).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(row[0]), float(row[1] or 0.0)

    def put(self, key: str, ns: str, value: Any, cost_inr: float = 0.0) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO entries (key, ns, value, cost_inr) VALUES (?, ?, ?, ?)", (key, ns, canonical_json(value), cost_inr))

    def blob_path(self, digest: str, suffix: str = ".wav") -> Path:
        return self.root / "blobs" / digest[:2] / f"{digest}{suffix}"

    def put_blob(self, data: bytes, suffix: str = ".wav") -> Path:
        digest = sha256_bytes(data)
        path = self.blob_path(digest, suffix)
        if not path.exists():
            atomic_write_bytes(path, data)
        return path

    def get_blob(self, digest: str, suffix: str = ".wav") -> bytes | None:
        path = self.blob_path(digest, suffix)
        return path.read_bytes() if path.exists() else None

    def close(self) -> None:
        with self._lock:
            self._db.close()
