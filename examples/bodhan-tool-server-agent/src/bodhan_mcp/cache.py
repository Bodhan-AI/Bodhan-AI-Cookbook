"""Content-addressed deterministic cache for Bodhan model calls.

Ensures safe execution, idempotency, and eliminates duplicate monetary spend.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from bodhan_mcp.config import config

logger = logging.getLogger("bodhan.cache")


class ContentAddressedCache:
    """Stores and retrieves tool execution results based on cryptographic hashes."""

    def __init__(self, cache_dir: Optional[Union[str, Path]] = None, enabled: Optional[bool] = None):
        self.cache_dir = Path(cache_dir or config.cache_dir)
        self.enabled = config.cache_enabled if enabled is None else enabled
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_hash(data: Union[str, bytes, Path]) -> str:
        """Compute SHA-256 hash of text, raw bytes, or file content."""
        hasher = hashlib.sha256()
        if isinstance(data, Path) or (isinstance(data, str) and Path(data).is_file()):
            path = Path(data)
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
        elif isinstance(data, str):
            hasher.update(data.encode("utf-8"))
        elif isinstance(data, bytes):
            hasher.update(data)
        else:
            hasher.update(str(data).encode("utf-8"))
        return hasher.hexdigest()

    def generate_key(
        self,
        tool_name: str,
        params: Dict[str, Any],
        content: Union[str, bytes, Path],
    ) -> str:
        """Generate a deterministic cache key from tool name, parameters, and content hash."""
        content_hash = self.compute_hash(content)
        # Normalize params to sorted JSON string for stable hashing
        normalized_params = json.dumps(params, sort_keys=True, default=str)
        composite = f"{tool_name}:{normalized_params}:{content_hash}"
        return hashlib.sha256(composite.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached output if available."""
        if not self.enabled:
            return None

        cache_file = self.cache_dir / f"{key}.json"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.debug("Cache hit for key: %s", key)
            return data
        except Exception as e:
            logger.warning("Error reading cache for key %s: %s", key, e)
            return None

    def set(self, key: str, data: Dict[str, Any]) -> None:
        """Persist result data under the specified key."""
        if not self.enabled:
            return

        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug("Cached result for key: %s", key)
        except Exception as e:
            logger.warning("Error writing cache for key %s: %s", key, e)

    def clear(self) -> int:
        """Clear all cached entries, returning the number of deleted items."""
        if not self.cache_dir.exists():
            return 0
        count = 0
        for item in self.cache_dir.glob("*.json"):
            try:
                item.unlink()
                count += 1
            except Exception:
                pass
        return count


# Global cache instance
cache = ContentAddressedCache()
