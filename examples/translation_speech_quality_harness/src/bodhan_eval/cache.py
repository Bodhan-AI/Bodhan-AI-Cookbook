"""Disk cache for every model output, keyed by the exact input.

The key is a SHA-256 of ``{kind, model, params, input}``. Values are JSON;
binary outputs (synthesised audio) are stored as files under ``blobs/`` and
referenced by hash. Each entry also records what the call cost, so a rerun
from cache can still report the value of the outputs it used while spending
nothing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


def stable_hash(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Cache:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "blobs").mkdir(exist_ok=True)
        self._lock = threading.Lock()
        # timeout: wait for another process (a parallel run sharing the cache) instead of failing on its lock
        self._db = sqlite3.connect(self.root / "cache.sqlite3", check_same_thread=False, timeout=30)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, kind TEXT, value TEXT, cost_inr REAL)"
        )
        self._db.commit()

    @staticmethod
    def key(kind: str, model: str, params: dict, payload: Any) -> str:
        return stable_hash({"kind": kind, "model": model, "params": params, "input": payload})

    def get(self, key: str) -> tuple[Any, float] | None:
        with self._lock:
            row = self._db.execute("SELECT value, cost_inr FROM entries WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return json.loads(row[0]), float(row[1] or 0.0)

    def put(self, key: str, kind: str, value: Any, cost_inr: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO entries (key, kind, value, cost_inr) VALUES (?, ?, ?, ?)",
                (key, kind, json.dumps(value, ensure_ascii=False), cost_inr),
            )
            self._db.commit()

    def put_blob(self, data: bytes, suffix: str = ".wav") -> str:
        digest = bytes_hash(data)
        path = self.root / "blobs" / f"{digest}{suffix}"
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return digest

    def blob_path(self, digest: str, suffix: str = ".wav") -> Path:
        return self.root / "blobs" / f"{digest}{suffix}"

    def close(self) -> None:
        with self._lock:
            self._db.close()
