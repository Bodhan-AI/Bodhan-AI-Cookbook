from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

CACHE_FORMAT_VERSION = 1
_DIR_MODE = 0o700
_FILE_MODE = 0o600


def cache_key(model: str, request: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"version": CACHE_FORMAT_VERSION, "model": model, "request": request},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ResultCache:
    def __init__(
        self,
        directory: Path | None,
        ttl_s: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = directory
        self.ttl_s = ttl_s
        self._clock = clock
        self._inflight: dict[str, asyncio.Future[Any]] = {}

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def _path(self, model: str, key: str, suffix: str) -> Path:
        assert self.directory is not None
        return self.directory / model / key[:2] / f"{key}{suffix}"

    def load(self, model: str, key: str, suffix: str) -> bytes | None:
        if self.directory is None:
            return None
        path = self._path(model, key, suffix)
        try:
            if self._clock() - path.stat().st_mtime > self.ttl_s:
                return None
            return path.read_bytes()
        except OSError:
            return None

    def store(self, model: str, key: str, suffix: str, data: bytes) -> None:
        if self.directory is None:
            return
        path = self._path(model, key, suffix)
        tmp: Path | None = None
        try:
            self.directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
            for folder in (self.directory / model, path.parent):
                folder.mkdir(mode=_DIR_MODE, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=suffix)
            tmp = Path(tmp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.chmod(tmp, _FILE_MODE)
            os.replace(tmp, path)
            tmp = None
        except OSError as exc:
            logger.warning("cache: could not store a %s result: %s", model, exc)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    async def single_flight(self, key: str, compute: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        running = self._inflight.get(key)
        if running is not None:
            return await asyncio.shield(running), False
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        future.add_done_callback(lambda done: done.cancelled() or done.exception())
        self._inflight[key] = future
        try:
            result = await compute()
        except asyncio.CancelledError:
            future.cancel()
            raise
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(result)
            return result, True
        finally:
            self._inflight.pop(key, None)
