"""Content-addressed cache of every model output, keyed by the exact input."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path


def stable_key(kind: str, payload: dict) -> str:
    blob = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _paths(self, kind: str, key: str):
        d = self.root / kind / key[:2]
        return d / f"{key}.json", d / f"{key}.bin"

    def get(self, kind: str, payload: dict):
        """Returns (value, cost_inr, blob|None) or None."""
        key = stable_key(kind, payload)
        jp, bp = self._paths(kind, key)
        if not jp.exists():
            return None
        try:
            rec = json.loads(jp.read_text(encoding="utf-8"))
            blob = bp.read_bytes() if bp.exists() else None
            return rec["value"], rec.get("cost_inr", 0.0), blob
        except (OSError, ValueError, KeyError):
            return None  # corrupt entry: treat as a miss

    def put(self, kind: str, payload: dict, value, cost_inr: float = 0.0, blob: bytes | None = None):
        key = stable_key(kind, payload)
        jp, bp = self._paths(kind, key)
        jp.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if blob is not None:
                _atomic_write(bp, blob)
            _atomic_write(jp, json.dumps({"value": value, "cost_inr": cost_inr}, ensure_ascii=False).encode("utf-8"))


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}{threading.get_ident()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)
