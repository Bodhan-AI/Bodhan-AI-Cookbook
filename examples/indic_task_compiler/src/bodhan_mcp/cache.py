"""Content-addressed disk cache for Bodhan API calls."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_cache_key(model: str, params: dict[str, Any], input_hash: str) -> str:
    canonical = json.dumps(
        {"model": model, "params": params, "input_hash": input_hash},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class CacheEntry:
    payload: dict[str, Any]
    binary: bytes | None = None
    original_cost_inr: float = 0.0


class DiskCache:
    """Simple crash-safe SHA-256 disk cache.

    Never stores API keys or headers. Cache hits return cost_inr=0.0 to the caller.
    """

    def __init__(self, cache_dir: Path | str | None = None, enabled: bool = True) -> None:
        if cache_dir is None:
            env_dir = os.environ.get("BODHAN_CACHE_DIR")
            cache_dir = Path(env_dir).expanduser() if env_dir else Path.home() / ".cache" / "bodhan_mcp"
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled

    def _entry_paths(self, key: str) -> tuple[Path, Path]:
        subdir = self.cache_dir / key[:2]
        return subdir / f"{key}.json", subdir / f"{key}.bin"

    def get(self, key: str) -> CacheEntry | None:
        if not self.enabled:
            return None
        json_path, bin_path = self._entry_paths(key)
        if not json_path.exists():
            return None
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            binary = bin_path.read_bytes() if meta.get("has_binary") and bin_path.exists() else None
            if meta.get("has_binary") and binary is None:
                return None
            return CacheEntry(
                payload=meta["payload"],
                binary=binary,
                original_cost_inr=float(meta.get("original_cost_inr", 0.0)),
            )
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    def put(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        binary: bytes | None = None,
        original_cost_inr: float = 0.0,
    ) -> None:
        if not self.enabled:
            return
        json_path, bin_path = self._entry_paths(key)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        if binary is not None:
            tmp_bin = bin_path.with_suffix(".bin.tmp")
            tmp_bin.write_bytes(binary)
            tmp_bin.replace(bin_path)
        meta = {
            "payload": payload,
            "has_binary": binary is not None,
            "original_cost_inr": round(original_cost_inr, 6),
        }
        tmp_json = json_path.with_suffix(".json.tmp")
        tmp_json.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        tmp_json.replace(json_path)
