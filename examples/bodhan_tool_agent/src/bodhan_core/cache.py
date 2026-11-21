"""Content-addressed disk cache so the same input is never sent to the same model twice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def make_key(model: str, *parts: Any) -> str:
    h = hashlib.sha256(model.encode())
    for p in parts:
        h.update(b"\x00")
        if isinstance(p, (bytes, bytearray)):
            h.update(p)
        else:
            h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode())
    return h.hexdigest()


class Cache:
    def __init__(self, directory: Path | None):
        self.dir = directory
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def get_json(self, key: str) -> Any | None:
        if not self.dir:
            return None
        p = self.dir / f"{key}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (OSError, ValueError):
            return None

    def put_json(self, key: str, value: Any) -> None:
        if self.dir:
            (self.dir / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def get_blob(self, key: str) -> bytes | None:
        if not self.dir:
            return None
        p = self.dir / f"{key}.bin"
        return p.read_bytes() if p.exists() else None

    def put_blob(self, key: str, data: bytes) -> None:
        if self.dir:
            (self.dir / f"{key}.bin").write_bytes(data)
