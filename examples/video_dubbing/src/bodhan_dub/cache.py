"""Content-addressed disk cache for model calls, so reruns never repeat a paid request.

The key is a sha256 of (model, parameters, input payload): change any of them and it's
a new entry, so a cache hit is always exactly the same request.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class DiskCache:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(model: str, params: dict[str, Any], payload: bytes | str) -> str:
        h = hashlib.sha256()
        h.update(model.encode())
        h.update(json.dumps(params, sort_keys=True, ensure_ascii=False).encode())
        h.update(payload.encode() if isinstance(payload, str) else payload)
        return h.hexdigest()

    def _path(self, key: str, suffix: str) -> Path:
        return self.root / key[:2] / f"{key}{suffix}"

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key, ".bin")
        return path.read_bytes() if path.exists() else None

    def put_bytes(self, key: str, data: bytes) -> None:
        self._atomic_write(self._path(key, ".bin"), data)

    def get_json(self, key: str) -> Any | None:
        path = self._path(key, ".json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put_json(self, key: str, value: Any) -> None:
        self._atomic_write(self._path(key, ".json"), json.dumps(value, ensure_ascii=False).encode())

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        # Write-then-rename so a crash or a parallel worker never leaves a half-written entry.
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
