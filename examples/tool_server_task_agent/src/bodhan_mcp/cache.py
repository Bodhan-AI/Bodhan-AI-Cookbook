"""On-disk, content-addressed cache. Key = sha256(model + canonical input), so the same input is never paid for twice."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from . import config


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def make_key(model: str, *parts: str | bytes) -> str:
    h = hashlib.sha256(model.encode() + b"\0")
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


class DiskCache:
    def __init__(self, root: Path | None = None):
        self._root = root
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @property
    def root(self) -> Path:
        return self._root or config.cache_dir()

    def _path(self, key: str, suffix: str) -> Path:
        return self.root / key[:2] / f"{key}{suffix}"

    def get_json(self, key: str) -> dict | None:
        p = self._path(key, ".json")
        if p.is_file():
            try:
                value = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            with self._lock:
                self.hits += 1
            return value
        with self._lock:
            self.misses += 1
        return None

    def put_json(self, key: str, value: dict) -> None:
        p = self._path(key, ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def get_file(self, key: str, suffix: str) -> Path | None:
        p = self._path(key, suffix)
        if p.is_file() and p.stat().st_size > 0:
            with self._lock:
                self.hits += 1
            return p
        with self._lock:
            self.misses += 1
        return None

    def put_bytes(self, key: str, suffix: str, data: bytes) -> Path:
        p = self._path(key, suffix)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        return p


cache = DiskCache()
