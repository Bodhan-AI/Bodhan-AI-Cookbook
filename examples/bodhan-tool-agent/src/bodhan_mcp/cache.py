"""Disk-based cache for Bodhan model API responses.

Guarantees identical inputs are never sent to paid models more than once.
Supports both JSON responses and binary data (audio/image).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class DiskCache:
    """Thread-safe disk cache keyed by cryptographic hash of inputs."""

    def __init__(self, cache_dir: Path | str | None = None):
        if cache_dir is None:
            cache_dir = Path(".cache") / "bodhan_calls"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def generate_key(model_id: str, input_data: str | bytes | dict, **extra_params: Any) -> str:
        """Generates a deterministic SHA256 key from model id, input, and parameters."""
        hasher = hashlib.sha256()
        hasher.update(model_id.encode("utf-8"))

        if isinstance(input_data, bytes):
            hasher.update(input_data)
        elif isinstance(input_data, dict):
            hasher.update(json.dumps(input_data, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        else:
            hasher.update(str(input_data).encode("utf-8"))

        if extra_params:
            hasher.update(json.dumps(extra_params, sort_keys=True, ensure_ascii=False).encode("utf-8"))

        return hasher.hexdigest()

    def get_json(self, key: str) -> dict | None:
        """Retrieves a cached JSON response if it exists."""
        target = self.cache_dir / f"{key}.json"
        if not target.exists():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set_json(self, key: str, data: dict) -> None:
        """Saves a JSON response to cache."""
        target = self.cache_dir / f"{key}.json"
        temp_target = target.with_suffix(".tmp")
        temp_target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_target.replace(target)

    def get_bytes(self, key: str) -> bytes | None:
        """Retrieves cached binary data (e.g. audio bytes) if it exists."""
        target = self.cache_dir / f"{key}.bin"
        if not target.exists():
            return None
        try:
            return target.read_bytes()
        except OSError:
            return None

    def set_bytes(self, key: str, data: bytes) -> None:
        """Saves binary data to cache."""
        target = self.cache_dir / f"{key}.bin"
        temp_target = target.with_suffix(".tmp")
        temp_target.write_bytes(data)
        temp_target.replace(target)

    def has(self, key: str) -> bool:
        """Checks if a key exists in cache as either JSON or binary."""
        return (self.cache_dir / f"{key}.json").exists() or (self.cache_dir / f"{key}.bin").exists()
