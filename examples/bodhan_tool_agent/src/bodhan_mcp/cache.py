from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional


class DiskCache:
    def __init__(self, cache_dir: Path, enabled: bool = True) -> None:
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        self._memory_json: Dict[str, Dict[str, Any]] = {}
        self._memory_bytes: Dict[str, bytes] = {}
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def compute_key(model: str, operation: str, payload: Dict[str, Any], raw_bytes: Optional[bytes] = None) -> str:
        hasher = hashlib.sha256()
        hasher.update(f"{model}:{operation}:".encode("utf-8"))
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        hasher.update(canonical.encode("utf-8"))
        if raw_bytes is not None:
            hasher.update(b":bytes:")
            hasher.update(hashlib.sha256(raw_bytes).digest())
        return hasher.hexdigest()

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        if key in self._memory_json:
            return self._memory_json[key]
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._memory_json[key] = data
                return data
            except Exception:
                return None
        return None

    def set_json(self, key: str, value: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        self._memory_json[key] = value
        path = self.cache_dir / f"{key}.json"
        try:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_bytes(self, key: str) -> Optional[bytes]:
        if not self.enabled:
            return None
        if key in self._memory_bytes:
            return self._memory_bytes[key]
        path = self.cache_dir / f"{key}.bin"
        if path.exists():
            try:
                data = path.read_bytes()
                self._memory_bytes[key] = data
                return data
            except Exception:
                return None
        return None

    def set_bytes(self, key: str, value: bytes) -> None:
        if not self.enabled:
            return
        self._memory_bytes[key] = value
        path = self.cache_dir / f"{key}.bin"
        try:
            path.write_bytes(value)
        except Exception:
            pass
