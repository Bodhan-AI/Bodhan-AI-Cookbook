"""Content-addressed cache: the same input is never sent to the same model twice."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import config


def digest(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def file_digest(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _path(key: str, ext: str) -> Path:
    return config.cache_dir() / key[:2] / f"{key}.{ext}"


def get_json(key: str) -> Any | None:
    p = _path(key, "json")
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


def put_json(key: str, value: Any) -> None:
    p = _path(key, "json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def get_bytes(key: str) -> bytes | None:
    p = _path(key, "bin")
    return p.read_bytes() if p.is_file() else None


def put_bytes(key: str, value: bytes) -> None:
    p = _path(key, "bin")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(value)
