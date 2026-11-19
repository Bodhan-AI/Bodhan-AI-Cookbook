"""
Deduplication and caching mechanism for tool calls to prevent redundant model invocations.
"""

import hashlib
import json
from typing import Dict, Any, Optional


class ToolCache:
    """In-memory and run-level cache for model calls."""

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self.hits: int = 0
        self.misses: int = 0

    def _hash_key(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        # Create deterministic JSON representation
        serialized = json.dumps({"tool": tool_name, "args": arguments}, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Any]:
        key = self._hash_key(tool_name, arguments)
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        self.misses += 1
        return None

    def set(self, tool_name: str, arguments: Dict[str, Any], result: Any) -> None:
        key = self._hash_key(tool_name, arguments)
        self._cache[key] = result
