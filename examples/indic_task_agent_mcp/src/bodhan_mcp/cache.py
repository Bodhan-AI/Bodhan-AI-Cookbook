"""Content-addressed disk cache for every Bodhan response.

The key is a SHA-256 over the model, endpoint, request parameters and the exact input bytes, so an identical
request is never sent twice (within a run or across reruns), and a rerun of an unchanged task costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def make_key(model: str, endpoint: str, params: dict[str, Any], payload: bytes = b"") -> str:
    h = hashlib.sha256()
    h.update(f"{model}\n{endpoint}\n".encode())
    h.update(json.dumps(params, sort_keys=True, ensure_ascii=False).encode())
    h.update(b"\n")
    h.update(payload)
    return h.hexdigest()


class DiskCache:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled

    def _path(self, key: str, suffix: str) -> Path:
        return self.root / key[:2] / f"{key}{suffix}"

    def get_json(self, key: str) -> Any | None:
        p = self._path(key, ".json")
        if not self.enabled or not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put_json(self, key: str, value: Any) -> None:
        if self.enabled:
            self._atomic_write(self._path(key, ".json"), json.dumps(value, ensure_ascii=False).encode())

    def get_bytes(self, key: str) -> bytes | None:
        p = self._path(key, ".bin")
        if not self.enabled or not p.exists():
            return None
        return p.read_bytes()

    def put_bytes(self, key: str, value: bytes) -> None:
        if self.enabled:
            self._atomic_write(self._path(key, ".bin"), value)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
