"""Disk cache keyed by the exact request. Re-runs and retries never pay twice."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import get_settings


def fingerprint(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, bytes):
            h.update(p)
        else:
            h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode())
        h.update(b"\x00")
    return h.hexdigest()


def _path(kind: str, key: str, ext: str) -> Path:
    return get_settings().cache_dir / kind / key[:2] / f"{key}.{ext}"


def get_json(kind: str, key: str) -> Any | None:
    p = _path(kind, key, "json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def put_json(kind: str, key: str, value: Any) -> None:
    p = _path(kind, key, "json")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def get_bytes(kind: str, key: str) -> bytes | None:
    p = _path(kind, key, "bin")
    return p.read_bytes() if p.exists() else None


def put_bytes(kind: str, key: str, value: bytes) -> None:
    p = _path(kind, key, "bin")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(value)
    tmp.replace(p)
