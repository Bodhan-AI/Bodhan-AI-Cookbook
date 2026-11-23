"""Disk cache of every model output, keyed by sha256 of (model, endpoint, canonical
params, exact input bytes). A rerun from cache is byte-identical and costs nothing.

Text payloads are stored as JSON; binary payloads (audio) are stored as raw bytes with a
sidecar .json for metadata. The cache key never depends on wall-clock or ordering.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def canonical_key(model: str, endpoint: str, params: dict[str, Any], payload: bytes | str) -> str:
    """Stable sha256 over the exact call. `params` are sorted; payload is hashed as bytes."""
    if isinstance(payload, str):
        payload_bytes = payload.encode("utf-8")
    else:
        payload_bytes = payload
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(endpoint.encode("utf-8"))
    h.update(b"\x00")
    h.update(json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    h.update(b"\x00")
    h.update(hashlib.sha256(payload_bytes).digest())
    return h.hexdigest()


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _path(self, key: str, suffix: str) -> Path:
        # shard by first 2 hex chars to avoid huge dirs
        d = self.root / key[:2]
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}{suffix}"

    # ---- JSON payloads (translate / transcribe responses) ----
    def get_json(self, key: str) -> Any | None:
        p = self._path(key, ".json")
        if p.exists():
            self.hits += 1
            with p.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        self.misses += 1
        return None

    def put_json(self, key: str, value: Any) -> None:
        p = self._path(key, ".json")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=p.parent,
                                         delete=False) as fh:
            tmp = Path(fh.name)
            try:
                json.dump(value, fh, ensure_ascii=False, sort_keys=True)
            except BaseException:
                fh.close()
                tmp.unlink(missing_ok=True)
                raise
        try:
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)

    # ---- binary payloads (audio) ----
    def get_bytes(self, key: str, suffix: str = ".bin") -> bytes | None:
        p = self._path(key, suffix)
        if p.exists():
            self.hits += 1
            return p.read_bytes()
        self.misses += 1
        return None

    def put_bytes(self, key: str, data: bytes, suffix: str = ".bin") -> Path:
        p = self._path(key, suffix)
        with tempfile.NamedTemporaryFile(mode="wb", dir=p.parent, delete=False) as fh:
            tmp = Path(fh.name)
            try:
                fh.write(data)
            except BaseException:
                fh.close()
                tmp.unlink(missing_ok=True)
                raise
        try:
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)
        return p

    def path_for(self, key: str, suffix: str) -> Path:
        return self._path(key, suffix)
