"""Content-addressed on-disk cache for tool responses."""

from __future__ import annotations

import hashlib
import json
import pathlib
import threading


class ToolCache:
    """Thread-safe, content-addressed file cache.

    Each entry is stored as ``<cache_dir>/<first2>/<rest>.bin`` where the key
    is the SHA-256 hex digest of the canonicalised request parameters.
    """

    def __init__(self, cache_dir: pathlib.Path) -> None:
        self._root = pathlib.Path(cache_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Key derivation
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(*parts: bytes | str) -> str:
        """Compute SHA-256 hex digest over arbitrary binary/text parts."""
        h = hashlib.sha256()
        for p in parts:
            if isinstance(p, str):
                h.update(p.encode("utf-8"))
            else:
                h.update(p)
        return h.hexdigest()

    def key(self, model: str, **kwargs: object) -> str:
        """Compute the cache key for *model* and the given keyword arguments.

        The key is the SHA-256 hex digest of ``json(sorted({model: model,
        **kwargs}))`` with ensure_ascii=False and sorted keys.
        """
        payload = {"_model": model, **kwargs}
        serialised = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Entry path helpers
    # ------------------------------------------------------------------

    def _entry_path(self, k: str) -> pathlib.Path:
        prefix, rest = k[:2], k[2:]
        return self._root / prefix / f"{rest}.bin"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has(self, k: str) -> bool:
        """Return True if the cache entry *k* exists."""
        return self._entry_path(k).exists()

    def get(self, k: str) -> bytes | None:
        """Return the cached bytes for key *k*, or None on a miss."""
        path = self._entry_path(k)
        with self._lock:
            if not path.exists():
                return None
            return path.read_bytes()

    def put(self, k: str, data: bytes) -> None:
        """Store *data* under key *k*.

        Creates any necessary parent directories.  Writing is atomic: data is
        written to a temporary file first and then renamed, so a partial write
        never corrupts an existing entry.
        """
        path = self._entry_path(k)
        tmp_path = path.with_suffix(".tmp")
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_bytes(data)
            tmp_path.replace(path)
