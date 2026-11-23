"""Content-addressed disk cache for model outputs.

The key is a SHA-256 over the model id, the canonical request parameters and the
exact input bytes, so the same input is never sent to the same model twice — in
one run or across runs. Writes are atomic (temp file + rename) so a crash never
leaves a half-written entry, and concurrent writers of the same key are harmless.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def cache_key(model: str, params: dict[str, Any], payload: bytes | str = b"") -> str:
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(b"\x00")
    h.update(json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    h.update(b"\x00")
    h.update(payload.encode() if isinstance(payload, str) else payload)
    return h.hexdigest()


class DiskCache:
    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, key: str, suffix: str) -> Path:
        return self.root / key[:2] / f"{key}{suffix}"

    def get_json(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        p = self._path(key, ".json")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.misses += 1
            return None
        self.hits += 1
        return data

    def put_json(self, key: str, value: dict[str, Any]) -> None:
        if self.enabled:
            self._atomic_write(self._path(key, ".json"), json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def get_bytes(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        p = self._path(key, ".bin")
        try:
            data = p.read_bytes()
        except OSError:
            self.misses += 1
            return None
        self.hits += 1
        return data

    def put_bytes(self, key: str, value: bytes) -> None:
        if self.enabled:
            self._atomic_write(self._path(key, ".bin"), value)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
