"""Content-addressed disk cache for Bodhan responses.

Key = sha256(model, endpoint, canonical params, sha256(input bytes)). The same input sent
to the same model with the same settings is answered from disk, within a run and across
runs, so retries of a whole task never pay twice. Entries are written atomically.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_key(model: str, endpoint: str, params: dict[str, Any], payload: bytes) -> str:
    blob = json.dumps(
        {"m": model, "e": endpoint, "p": params, "h": sha256_bytes(payload)}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, model: str, key: str, suffix: str) -> Path:
        return self.root / model / key[:2] / f"{key}{suffix}"

    def get(self, model: str, key: str, suffix: str) -> bytes | None:
        if not self.enabled:
            return None
        path = self._path(model, key, suffix)
        try:
            data = path.read_bytes()
        except OSError:
            self.misses += 1
            return None
        self.hits += 1
        return data

    def put(self, model: str, key: str, suffix: str, data: bytes) -> None:
        if not self.enabled:
            return
        path = self._path(model, key, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def get_json(self, model: str, key: str) -> Any | None:
        raw = self.get(model, key, ".json")
        return None if raw is None else json.loads(raw.decode("utf-8"))

    def put_json(self, model: str, key: str, value: Any) -> None:
        self.put(model, key, ".json", json.dumps(value, ensure_ascii=False).encode("utf-8"))
