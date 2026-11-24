from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse


@dataclass
class CacheRecord:
    result: dict[str, Any]
    artifact_path: str | None = None
    artifact_sha256: str | None = None
    cost_inr: float = 0.0
    created_at: str | None = None
    cache_version: int = 1


class CacheBackend(Protocol):
    def get(self, key: str) -> CacheRecord | None: ...
    def set(self, key: str, record: CacheRecord, ttl_seconds: int) -> None: ...
    def get_artifact(self, file_id: str) -> dict[str, Any] | None: ...
    def set_artifact(self, file_id: str, artifact: dict[str, Any], ttl_seconds: int) -> None: ...
    def delete_artifact(self, file_id: str) -> None: ...
    def list_artifacts(self, owner_user_id: str | None) -> list[dict[str, Any]]: ...


class MemoryBackend:
    def __init__(self) -> None:
        self._items: dict[str, CacheRecord] = {}
        self._artifacts: dict[str, tuple[dict[str, Any], float | None]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> CacheRecord | None:
        with self._lock:
            return self._items.get(key)

    def set(self, key: str, record: CacheRecord, ttl_seconds: int) -> None:
        del ttl_seconds
        with self._lock:
            self._items[key] = record

    def get_artifact(self, file_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._artifacts.get(file_id)
            if not entry:
                return None
            artifact, expires_at = entry
            if expires_at is not None and expires_at <= time.time():
                self._artifacts.pop(file_id, None)
                return None
            return artifact

    def set_artifact(self, file_id: str, artifact: dict[str, Any], ttl_seconds: int) -> None:
        with self._lock:
            expires_at = time.time() + ttl_seconds if ttl_seconds > 0 else None
            self._artifacts[file_id] = (artifact, expires_at)

    def delete_artifact(self, file_id: str) -> None:
        with self._lock:
            self._artifacts.pop(file_id, None)

    def list_artifacts(self, owner_user_id: str | None) -> list[dict[str, Any]]:
        with self._lock:
            now = time.time()
            items = []
            for file_id, (artifact, expires_at) in list(self._artifacts.items()):
                if expires_at is not None and expires_at <= now:
                    self._artifacts.pop(file_id, None)
                elif artifact.get("owner_user_id") == owner_user_id:
                    items.append({"file_id": file_id, **artifact})
            return items


class RedisBackend:
    def __init__(self, url: str) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError("Redis backend requires redis==5.2.1; install requirements.txt") from exc
        self.client = redis.Redis.from_url(url, decode_responses=True)
        try:
            self.client.ping()
            return
        except redis.exceptions.ConnectionError:
            if os.getenv("BODHAN_REDIS_AUTOSTART", "true").lower() not in {"1", "true", "yes", "on"}:
                raise RuntimeError("Redis is unavailable and BODHAN_REDIS_AUTOSTART is disabled")
        self._start_local_redis(url)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                self.client.ping()
                return
            except redis.exceptions.ConnectionError:
                time.sleep(0.1)
        raise RuntimeError(f"Could not start or connect to Redis at {url}")

    @staticmethod
    def _start_local_redis(url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        if host not in {"localhost", "127.0.0.1", "::1"} or parsed.password or parsed.username:
            raise RuntimeError(f"Redis is unavailable at {url}; automatic startup is only supported for unauthenticated local Redis")
        candidates = [shutil.which("redis-server"), "/opt/homebrew/bin/redis-server", "/usr/local/bin/redis-server", "/usr/bin/redis-server"]
        executable = next((item for item in candidates if item and Path(item).is_file()), None)
        if not executable:
            raise RuntimeError("Redis is unavailable and redis-server was not found. Install Redis or set BODHAN_CACHE_BACKEND=memory.")
        port = parsed.port or 6379
        bind_host = "::1" if host == "::1" else "127.0.0.1"
        data_dir = Path.home() / ".cache" / "bodhan-setu" / "redis"
        data_dir.mkdir(parents=True, exist_ok=True)
        command = [
            executable,
            "--bind", bind_host,
            "--port", str(port),
            "--protected-mode", "yes",
            "--daemonize", "yes",
            "--dir", str(data_dir),
            "--dbfilename", "dump.rdb",
            "--appendonly", "yes",
            "--appendfilename", "appendonly.aof",
            "--pidfile", str(data_dir / f"redis-{port}.pid"),
            "--logfile", str(data_dir / f"redis-{port}.log"),
        ]
        try:
            subprocess.run(command, check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Failed to start local Redis: {exc}") from exc

    def get(self, key: str) -> CacheRecord | None:
        raw = self.client.get(key)
        return CacheRecord(**json.loads(raw)) if raw else None

    def set(self, key: str, record: CacheRecord, ttl_seconds: int) -> None:
        self.client.setex(key, ttl_seconds, json.dumps(asdict(record), ensure_ascii=False))

    def get_artifact(self, file_id: str) -> dict[str, Any] | None:
        raw = self.client.get(f"bodhan_setu:artifact:{file_id}")
        return json.loads(raw) if raw else None

    def set_artifact(self, file_id: str, artifact: dict[str, Any], ttl_seconds: int) -> None:
        key = f"bodhan_setu:artifact:{file_id}"
        value = json.dumps(artifact, ensure_ascii=False)
        if ttl_seconds > 0:
            self.client.setex(key, ttl_seconds, value)
        else:
            self.client.set(key, value)

    def delete_artifact(self, file_id: str) -> None:
        self.client.delete(f"bodhan_setu:artifact:{file_id}")

    def list_artifacts(self, owner_user_id: str | None) -> list[dict[str, Any]]:
        prefix = "bodhan_setu:artifact:"
        items = []
        for key in self.client.scan_iter(match=f"{prefix}*", count=200):
            raw = self.client.get(key)
            if not raw:
                continue
            artifact = json.loads(raw)
            if artifact.get("owner_user_id") == owner_user_id:
                items.append({"file_id": key.removeprefix(prefix), **artifact})
        return items


class CacheStore:
    def __init__(self, backend: CacheBackend, ttl_seconds: int = 86_400) -> None:
        self.backend = backend
        self.ttl_seconds = ttl_seconds

    def get(self, key: str) -> CacheRecord | None:
        record = self.backend.get(key)
        if not record:
            return None
        if record.artifact_path and record.artifact_sha256:
            path = Path(record.artifact_path)
            if not path.is_file() or sha256_file(path) != record.artifact_sha256:
                return None
        return record

    def set(self, key: str, record: CacheRecord) -> None:
        self.backend.set(key, record, self.ttl_seconds)

    def get_artifact(self, file_id: str) -> dict[str, Any] | None:
        return self.backend.get_artifact(file_id)

    def set_artifact(self, file_id: str, artifact: dict[str, Any], ttl_seconds: int | None = None) -> None:
        self.backend.set_artifact(file_id, artifact, self.ttl_seconds if ttl_seconds is None else ttl_seconds)

    def delete_artifact(self, file_id: str) -> None:
        self.backend.delete_artifact(file_id)

    def list_artifacts(self, owner_user_id: str | None) -> list[dict[str, Any]]:
        return self.backend.list_artifacts(owner_user_id)


_CACHE: CacheStore | None = None


def get_cache() -> CacheStore:
    global _CACHE
    if _CACHE is None:
        backend_name = os.getenv("BODHAN_CACHE_BACKEND", "memory").lower()
        ttl = int(os.getenv("BODHAN_CACHE_TTL_SECONDS", "86400"))
        if backend_name == "redis":
            backend: CacheBackend = RedisBackend(os.getenv("BODHAN_REDIS_URL", "redis://localhost:6379/0"))
        elif backend_name in {"memory", "none"}:
            backend = MemoryBackend()
        else:
            raise ValueError(f"Unsupported BODHAN_CACHE_BACKEND: {backend_name}")
        _CACHE = CacheStore(backend, ttl)
    return _CACHE


def stable_key(tool: str, model: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"bodhan_setu:v1:{tool}:{model}:{digest}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
