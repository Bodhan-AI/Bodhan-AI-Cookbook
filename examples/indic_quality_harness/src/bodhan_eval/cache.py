"""Small persistent JSON caches shared by the pipeline stages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def stable_hash(payload: dict) -> str:
    """SHA-256 of a canonical JSON encoding (sorted keys, UTF-8)."""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class JsonCache:
    """A dict persisted to a JSON file.

    Writes are atomic (temp file + rename), so an interrupted run never
    leaves a half-written cache behind. A corrupt cache file is moved aside
    and treated as empty rather than crashing the run.
    """

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self.data: dict = {}
        self.hits = 0
        self.misses = 0

        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self.data, dict):
                    raise ValueError("cache root is not an object")
            except (json.JSONDecodeError, ValueError):
                backup = self.path.with_suffix(self.path.suffix + ".corrupt")
                self.path.replace(backup)
                print(f"Warning: cache {self.path} was unreadable; moved to {backup}")
                self.data = {}

    def get(self, key: str):
        value = self.data.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".cache-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def stats(self) -> dict:
        return {"entries": len(self.data), "hits": self.hits, "misses": self.misses}
