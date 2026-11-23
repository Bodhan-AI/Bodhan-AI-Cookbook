"""Content-addressed cache.

Key = SHA-256 of the exact request (model id, parameters, input bytes). If the
same input is ever sent to the same model twice — across languages, across
retries, across reruns — the second call is free.

This is what makes an iterative build affordable on a small credit balance, and
what makes a rerun deterministic.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "bodhan-dub/cache/v1"


@dataclass(frozen=True)
class CacheEntry:
    key: str
    path: Path
    data: Any
    meta: dict[str, Any]


class Cache:
    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = True,
        read_only: bool = False,
        force_refresh: bool = False,
    ) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.read_only = read_only
        self.force_refresh = force_refresh
        if enabled and not read_only:
            self.root.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    # -- keys -------------------------------------------------------------

    @staticmethod
    def make_key(*parts: bytes | str) -> str:
        digest = hashlib.sha256()
        for part in parts:
            chunk = part.encode("utf-8") if isinstance(part, str) else part
            digest.update(len(chunk).to_bytes(8, "big"))
            digest.update(chunk)
        return digest.hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    # -- json entries -----------------------------------------------------

    def get(self, key: str) -> CacheEntry | None:
        if not self.enabled or self.force_refresh:
            self.misses += 1
            return None
        path = self._path(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupted entry is a cache miss, never a crash.
            self.misses += 1
            return None
        if payload.get("schema") != SCHEMA:
            self.misses += 1
            return None
        self.hits += 1
        return CacheEntry(key=key, path=path, data=payload.get("data"), meta=payload.get("meta", {}))

    def put(self, key: str, data: Any, meta: dict[str, Any] | None = None) -> None:
        if not self.enabled or self.read_only:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": SCHEMA, "key": key, "data": data, "meta": meta or {}}
        _atomic_write(path, json.dumps(payload, ensure_ascii=False))

    # -- binary entries ---------------------------------------------------

    def get_bytes(self, key: str) -> bytes | None:
        if not self.enabled or self.force_refresh:
            self.misses += 1
            return None
        path = self._path(key).with_suffix(".bin")
        if not path.exists():
            self.misses += 1
            return None
        self.hits += 1
        return path.read_bytes()

    def put_bytes(self, key: str, data: bytes, meta: dict[str, Any] | None = None) -> None:
        if not self.enabled or self.read_only:
            return
        path = self._path(key).with_suffix(".bin")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".bin.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        if meta is not None:
            self.put(key, {"bytes": len(data)}, meta)

    # -- reporting --------------------------------------------------------

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "enabled": int(self.enabled)}


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
