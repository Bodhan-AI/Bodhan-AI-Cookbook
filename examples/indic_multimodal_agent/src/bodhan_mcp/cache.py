"""Disk cache layer for Bodhan model API responses.
Ensures we never send the identical input to the same model twice in a run.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional, Tuple
from bodhan_mcp.config import settings


class DiskCache:
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or settings.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _hash_key(self, endpoint: str, model: str, data: Any) -> str:
        """Create a deterministic SHA-256 hash for the request."""
        serialized = json.dumps(
            {"endpoint": endpoint, "model": model, "data": data},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get_json(self, endpoint: str, model: str, data: Any) -> Optional[dict]:
        """Retrieve cached JSON response if exists."""
        key = self._hash_key(endpoint, model, data)
        file_path = self.cache_dir / f"{key}.json"
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def set_json(self, endpoint: str, model: str, data: Any, response: dict) -> None:
        """Save JSON response to cache."""
        key = self._hash_key(endpoint, model, data)
        file_path = self.cache_dir / f"{key}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(response, f, ensure_ascii=False)
        except Exception:
            pass

    def get_bytes(self, endpoint: str, model: str, data: Any) -> Optional[bytes]:
        """Retrieve cached binary response (e.g. audio stream) if exists."""
        key = self._hash_key(endpoint, model, data)
        file_path = self.cache_dir / f"{key}.bin"
        if file_path.exists():
            try:
                with open(file_path, "rb") as f:
                    return f.read()
            except Exception:
                return None
        return None

    def set_bytes(self, endpoint: str, model: str, data: Any, response_bytes: bytes) -> None:
        """Save binary response to cache."""
        key = self._hash_key(endpoint, model, data)
        file_path = self.cache_dir / f"{key}.bin"
        try:
            with open(file_path, "wb") as f:
                f.write(response_bytes)
        except Exception:
            pass


cache = DiskCache()

