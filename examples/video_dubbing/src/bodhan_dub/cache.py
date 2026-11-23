"""Content-addressed on-disk cache for resumable runs (--resume).

Keyed by a stable hash of (stage, payload), so re-running after a crash reuses
completed ASR/MT/TTS work instead of re-spending credit. A no-op NullCache keeps
call sites clean when caching is disabled.
"""

from __future__ import annotations

import hashlib
import json
import os


class NullCache:
    def key(self, *a) -> str: return ""
    def get_json(self, k): return None
    def set_json(self, k, v): pass
    def get_bytes(self, k): return None
    def set_bytes(self, k, v): pass


class DiskCache:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def key(self, stage: str, payload: bytes | str) -> str:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        h = hashlib.sha1(stage.encode() + b"\x00" + payload).hexdigest()
        return f"{stage}_{h}"

    def _path(self, k: str, ext: str) -> str:
        return os.path.join(self.root, f"{k}.{ext}")

    def get_json(self, k: str):
        p = self._path(k, "json")
        if k and os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return None

    def set_json(self, k: str, value) -> None:
        if not k:
            return
        with open(self._path(k, "json"), "w") as f:
            json.dump(value, f, ensure_ascii=False)

    def get_bytes(self, k: str):
        p = self._path(k, "bin")
        if k and os.path.exists(p):
            with open(p, "rb") as f:
                return f.read()
        return None

    def set_bytes(self, k: str, value: bytes) -> None:
        if not k:
            return
        with open(self._path(k, "bin"), "wb") as f:
            f.write(value)
