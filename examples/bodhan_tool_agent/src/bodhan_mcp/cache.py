"""Content-addressed response cache.

A key is the sha256 of (namespace, canonical request parameters, input bytes),
so identical work maps to the same entry no matter which file path or tool it
came from. Entries live on disk under Settings.cache_dir and survive restarts;
a process-local layer keeps the "never pay twice in one run" promise even
when the disk cache is turned off.

Concurrent requests for the same key share one in-flight computation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def cache_key(namespace: str, params: dict[str, Any], data: bytes | None = None) -> str:
    h = hashlib.sha256()
    h.update(namespace.encode())
    h.update(b"\0")
    h.update(json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    h.update(b"\0")
    if data is not None:
        h.update(data)
    return h.hexdigest()


class DiskCache:
    def __init__(self, cache_dir: Path, enabled: bool = True) -> None:
        self.dir = Path(cache_dir)
        self.enabled = enabled
        self._memory: dict[str, bytes] = {}
        self._inflight: dict[str, asyncio.Future[tuple[bytes, bool]]] = {}

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.bin"

    def get(self, key: str) -> bytes | None:
        if key in self._memory:
            return self._memory[key]
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            data = path.read_bytes()
        except OSError:
            return None
        self._memory[key] = data
        return data

    def contains(self, key: str) -> bool:
        return key in self._memory or (self.enabled and self._path(key).is_file())

    def put(self, key: str, value: bytes) -> None:
        self._memory[key] = value
        if not self.enabled:
            return
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a crash never leaves a half-written entry behind.
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(value)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("cache write failed for %s: %s", key[:12], exc)

    async def get_or_compute(self, key: str, compute: Callable[[], Awaitable[bytes]]) -> tuple[bytes, bool]:
        """Return (value, hit). `hit` is True for cache hits and for joins on an in-flight request."""
        cached = self.get(key)
        if cached is not None:
            return cached, True
        pending = self._inflight.get(key)
        if pending is not None and pending.get_loop() is asyncio.get_running_loop():
            value, _ = await asyncio.shield(pending)
            return value, True

        fut: asyncio.Future[tuple[bytes, bool]] = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            value = await compute()
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                fut.cancel()
            elif not fut.done():
                fut.set_exception(exc)
                # Waiters re-raise it; mark it retrieved so a lone failure isn't logged as unhandled.
                fut.exception()
            raise
        else:
            self.put(key, value)
            fut.set_result((value, False))
            return value, False
        finally:
            if self._inflight.get(key) is fut:
                del self._inflight[key]
