"""Content-hash-based disk cache.

Cache key = SHA-256 of (model_name + sorted_params_json + file_content_or_text).
Entries stored as JSON in ~/.cache/bodhan/ keyed by hex digest.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.environ.get("BODHAN_CACHE_DIR", Path.home() / ".cache" / "bodhan"))


def _ensure_cache_dir() -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def _make_key(model: str, params: dict, content: bytes | str = b"") -> str:
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(json.dumps(params, sort_keys=True).encode())
    if isinstance(content, str):
        content = content.encode()
    h.update(content)
    return h.hexdigest()


def cache_get(model: str, params: dict, content: bytes | str = b"") -> Optional[Any]:
    key = _make_key(model, params, content)
    path = _ensure_cache_dir() / f"{key}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            logger.debug("Cache HIT for model=%s key=%s", model, key[:12])
            return data
        except Exception as exc:
            logger.warning("Cache read error for %s: %s", key[:12], exc)
    return None


def cache_set(model: str, params: dict, content: bytes | str, value: Any) -> None:
    key = _make_key(model, params, content)
    path = _ensure_cache_dir() / f"{key}.json"
    try:
        path.write_text(json.dumps(value))
        logger.debug("Cache SET for model=%s key=%s", model, key[:12])
    except Exception as exc:
        logger.warning("Cache write error for %s: %s", key[:12], exc)


def file_content_hash(path: str) -> str:
    """SHA-256 of a file's content (for artifact naming)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
