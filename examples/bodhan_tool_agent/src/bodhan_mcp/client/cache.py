"""Content-addressable in-memory and disk cache.

Ensures the take-home rule:
'The same input is never sent to the same model twice in a run'.
Cached results return cost = 0.0 INR and flag cached = True.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional


class ContentCache:
    """Thread-safe in-memory cache with optional file backing."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir
        self._memory: Dict[str, Any] = {}
        self._lock = threading.Lock()
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_hash(model: str, key_content: str | bytes, params: Optional[Dict[str, Any]] = None) -> str:
        """Create a deterministic SHA-256 fingerprint for a request."""
        hasher = hashlib.sha256()
        hasher.update(model.encode("utf-8"))
        if isinstance(key_content, bytes):
            hasher.update(key_content)
        else:
            hasher.update(key_content.encode("utf-8"))
        if params:
            serialized_params = json.dumps(params, sort_keys=True, ensure_ascii=False)
            hasher.update(serialized_params.encode("utf-8"))
        return hasher.hexdigest()

    def get(self, key_hash: str) -> Optional[Any]:
        """Retrieve a cached result by hash."""
        with self._lock:
            if key_hash in self._memory:
                return self._memory[key_hash]
            if self.cache_dir:
                disk_path = self.cache_dir / f"{key_hash}.json"
                if disk_path.exists():
                    try:
                        data = json.loads(disk_path.read_text(encoding="utf-8"))
                        self._memory[key_hash] = data
                        return data
                    except Exception:
                        pass
        return None

    def set(self, key_hash: str, value: Any, persist_disk: bool = True) -> None:
        """Save a result to memory and optionally disk."""
        with self._lock:
            self._memory[key_hash] = value
            if persist_disk and self.cache_dir:
                try:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    disk_path = self.cache_dir / f"{key_hash}.json"
                    disk_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

    def clear(self) -> None:
        """Reset the in-memory cache."""
        with self._lock:
            self._memory.clear()


# Default singleton cache instance with persistent disk backing (override via BODHAN_CACHE_DIR)
_DEFAULT_CACHE_DIR = os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")
GLOBAL_CACHE = ContentCache(cache_dir=Path(_DEFAULT_CACHE_DIR) if _DEFAULT_CACHE_DIR else None)
