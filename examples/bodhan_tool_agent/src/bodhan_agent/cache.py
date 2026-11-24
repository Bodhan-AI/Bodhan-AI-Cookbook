"""Deduplication and result caching using SHA-256 signatures."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("bodhan_agent.cache")


class ResultCache:
    """Caches tool execution results keyed by tool name, model identity, and argument hashes."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or Path(".cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "tool_call_cache.json"
        self._entries: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _persist(self):
        try:
            self.cache_file.write_text(json.dumps(self._entries, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to persist cache: {e}")

    def compute_key(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Derive a reproducible SHA-256 hash for a tool call."""
        # Normalize file inputs to their file content hashes if paths exist
        normalized_args = {}
        for k, v in sorted(arguments.items()):
            if k in {"file_path", "audio_path", "image_path"} and isinstance(v, str):
                p = Path(v)
                if p.exists() and p.is_file():
                    h = hashlib.sha256(p.read_bytes()).hexdigest()
                    normalized_args[k] = f"file_sha256:{h}"
                else:
                    normalized_args[k] = v
            else:
                normalized_args[k] = v

        serialized = json.dumps({"tool": tool_name, "args": normalized_args}, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Retrieve cached result if available."""
        key = self.compute_key(tool_name, arguments)
        if key in self._entries:
            logger.info(f"Cache hit for tool {tool_name} (key: {key[:8]}...)")
            return self._entries[key]
        return None

    def set(self, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]):
        """Store result in cache."""
        key = self.compute_key(tool_name, arguments)
        self._entries[key] = result
        self._persist()
