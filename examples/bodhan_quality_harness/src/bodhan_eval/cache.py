"""Content-addressed cache.

Every paid model call is keyed by the exact input, so a rerun costs nothing and
produces a byte-identical report — one of the stated quality targets. Audio is
cached as bytes alongside a small JSON sidecar.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class Cache:
    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: object) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update(repr(p).encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def _paths(self, key: str, suffix: str) -> tuple[Path, Path]:
        d = self.root / key[:2]
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}{suffix}", d / f"{key}.json"

    def get_json(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        _, meta = self._paths(key, ".json")
        if meta.exists():
            self.hits += 1
            return json.loads(meta.read_text(encoding="utf-8"))
        self.misses += 1
        return None

    def put_json(self, key: str, value: dict) -> None:
        if not self.enabled:
            return
        _, meta = self._paths(key, ".json")
        meta.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def get_bytes(self, key: str, suffix: str = ".wav") -> bytes | None:
        if not self.enabled:
            return None
        blob, meta = self._paths(key, suffix)
        if blob.exists() and meta.exists():
            self.hits += 1
            return blob.read_bytes()
        self.misses += 1
        return None

    def put_bytes(self, key: str, data: bytes, meta: dict, suffix: str = ".wav") -> None:
        if not self.enabled:
            return
        blob, metafile = self._paths(key, suffix)
        blob.write_bytes(data)
        metafile.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }
