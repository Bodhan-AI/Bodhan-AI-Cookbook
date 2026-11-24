"""Content-addressed cache: every model output is stored under a hash of its exact request."""

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path


def key_for(*parts) -> str:
    """Stable key from JSON-serialisable parts (bytes are hashed first)."""
    norm = [hashlib.sha256(p).hexdigest() if isinstance(p, bytes) else p for p in parts]
    return hashlib.sha256(json.dumps(norm, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Cache:
    def __init__(self, directory: str | Path | None):
        self.dir = Path(directory) if directory else None
        self._mem: dict[str, tuple[dict, bytes | None]] = {}
        self._lock = threading.Lock()
        if self.dir:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.dir = None  # not writable: memory only

    def _paths(self, key):
        sub = self.dir / key[:2]
        return sub / f"{key}.json", sub / f"{key}.bin"

    def get(self, key) -> tuple[dict, bytes | None] | None:
        with self._lock:
            if key in self._mem:
                return self._mem[key]
        if not self.dir:
            return None
        meta_p, bin_p = self._paths(key)
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            blob = bin_p.read_bytes() if meta.get("_has_blob") else None
        except (OSError, ValueError):
            return None
        with self._lock:
            self._mem[key] = (meta, blob)
        return meta, blob

    def put(self, key, meta: dict, blob: bytes | None = None):
        meta = {**meta, "_has_blob": blob is not None}
        with self._lock:
            self._mem[key] = (meta, blob)
        if not self.dir:
            return
        meta_p, bin_p = self._paths(key)
        meta_p.parent.mkdir(parents=True, exist_ok=True)
        if blob is not None:
            _atomic_write(bin_p, blob)
        _atomic_write(meta_p, json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _atomic_write(path: Path, data: bytes):
    # Write then rename, so a crash never leaves a half-written entry.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
