"""Content-addressed disk cache so a re-run never pays for the same Bodhan call twice."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path


def cache_key(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        data = part if isinstance(part, bytes) else json.dumps(part, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


class DiskCache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / key

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def put(self, key: str, value: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{key}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(value)
        tmp.replace(path)


class NoCache:
    def get(self, key: str) -> bytes | None:
        return None

    def put(self, key: str, value: bytes) -> None:
        return None
