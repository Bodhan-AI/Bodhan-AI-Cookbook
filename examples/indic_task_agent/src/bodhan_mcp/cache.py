"""Disk cache. The same input to the same model is never paid for twice."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def make_key(*parts: Any) -> str:
    """Stable hash of the parts. Bytes (audio, images) are hashed first, not stored."""
    norm = [hashlib.sha256(p).hexdigest() if isinstance(p, bytes) else p for p in parts]
    blob = json.dumps(norm, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str, ext: str) -> Path:
        return self.root / key[:2] / f"{key}.{ext}"

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # atomic: a half-written file is never read

    def get_bytes(self, key: str) -> bytes | None:
        p = self._path(key, "bin")
        return p.read_bytes() if p.exists() else None

    def put_bytes(self, key: str, data: bytes) -> None:
        self._write(self._path(key, "bin"), data)

    def get_json(self, key: str) -> Any | None:
        p = self._path(key, "json")
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put_json(self, key: str, value: Any) -> None:
        self._write(self._path(key, "json"), json.dumps(value, ensure_ascii=False).encode("utf-8"))