"""High-Performance Zero-Dependency Caching Layer for Bodhan Indic MCP Server.

Provides multi-process safe SQLite WAL caching for:
  - transcribe_audio (indic-transcribe)
  - translate_text (indic-translate)
  - synthesize_speech (indic-speak)
  - extract_document_text (indic-ocr)

Standards compliance: Python Standard Library Only (sqlite3, hashlib, unicodedata,
os, sys, time, pathlib, json, functools, inspect, contextlib, dataclasses).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from typing import Any, Callable, Coroutine, Literal
import unicodedata

from src.common.audio import inspect_audio
from src.common.logger import get_logger
from src.common.paths import resolve_input_path

logger = get_logger("mcp")

# Default voice catalog mapping for Indic-Speak TTS
DEFAULT_VOICES: dict[str, str] = {
    "hi": "Kavya", "ta": "Anitha", "te": "Sravani", "bn": "Ishita", "mr": "Anagha",
    "gu": "Dhara", "kn": "Deepika", "ml": "Lakshmi", "pa": "Kaur", "ur": "Saba",
    "as": "Prastuti", "doi": "Preeti", "kok": "Anjali", "mai": "Vaidehi", "ne": "Srijana",
    "or": "Itishree", "sa": "Bharati", "en": "Amit",
}


# ============================================================================
# Canonicalization and Key Generation Utilities
# ============================================================================

def canonicalize_text(text: str) -> str:
    """Normalize text using Unicode NFC and standardized Unix newlines.
    
    Ensures identical Indic conjuncts and diacritics produce identical byte sequences,
    and strips trailing whitespace on each line.
    """
    normalized = unicodedata.normalize("NFC", text.strip())
    return "\n".join(line.rstrip() for line in normalized.splitlines())


def hash_file_contents(file_path: Path | str, chunk_size: int = 65536) -> tuple[str, int]:
    """Compute SHA-256 hash and byte size of a file using streaming 64KB chunks.
    
    Prevents memory spikes on large audio files or scanned multi-page documents.
    """
    path = Path(file_path).resolve()
    hasher = hashlib.sha256()
    total_bytes = 0
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
            total_bytes += len(chunk)
    return hasher.hexdigest(), total_bytes


def make_translate_key(
    text: str,
    target_language: str,
    source_language: str | None = None,
    **kwargs: Any,
) -> str:
    """Build canonical content-addressed cache key for translate_text."""
    norm_text = canonicalize_text(text)
    payload = {
        "tool": "translate_text",
        "model": "indic-translate",
        "v": 1,
        "payload_hash": hashlib.sha256(norm_text.encode("utf-8")).hexdigest(),
        "target_language": target_language.strip().lower(),
        "source_language": (source_language or "auto").strip().lower(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_transcribe_key(
    file_path: Path | str,
    language: str | None = None,
    task: str = "transcribe",
    output_format: str = "text",
    output_path: str | None = None,
    **kwargs: Any,
) -> str:
    """Build canonical content-addressed cache key for transcribe_audio.

    output_path is deliberately excluded so identical audio transcribed to a
    different destination filename hits the cache instead of re-paying.
    output_format, task, language and file identity remain part of the key.
    """
    path = resolve_input_path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    file_hash, byte_size = hash_file_contents(path)
    payload = {
        "tool": "transcribe_audio",
        "model": "indic-transcribe",
        "v": 1,
        "payload_hash": file_hash,
        "byte_size": byte_size,
        "language": (language or "auto").strip().lower(),
        "task": (task or "transcribe").strip().lower(),
        "output_format": (output_format or "text").strip().lower(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_synthesize_key(
    text: str,
    language: str,
    voice: str | None = None,
    style: str = "news",
    **kwargs: Any,
) -> str:
    """Build canonical content-addressed cache key for synthesize_speech.
    
    output_path is deliberately excluded so identical speech synthesis hits
    cache regardless of requested output filename.
    """
    norm_text = canonicalize_text(text)
    lang = language.strip().lower()
    eff_voice = (voice or DEFAULT_VOICES.get(lang, "Kavya")).strip()
    eff_style = (style or "news").strip().lower()
    payload = {
        "tool": "synthesize_speech",
        "model": "indic-speak",
        "v": 1,
        "payload_hash": hashlib.sha256(norm_text.encode("utf-8")).hexdigest(),
        "language": lang,
        "voice": eff_voice,
        "style": eff_style,
        "sample_rate": 24000,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_extract_document_key(
    file_path: Path | str,
    table_format: str = "markdown",
    page_limit: int | None = None,
    **kwargs: Any,
) -> str:
    """Build canonical content-addressed cache key for extract_document_text."""
    path = resolve_input_path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    file_hash, byte_size = hash_file_contents(path)
    payload = {
        "tool": "extract_document_text",
        "model": "indic-ocr",
        "v": 1,
        "payload_hash": file_hash,
        "byte_size": byte_size,
        "table_format": (table_format or "markdown").strip().lower(),
        "page_limit": page_limit,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ============================================================================
# Cache Entry Data Model
# ============================================================================

@dataclass(frozen=True)
class CacheEntry:
    key: str
    tool_name: str
    input_summary: str
    response_data: str
    response_blob: bytes | None
    created_at: float
    last_accessed_at: float
    access_count: int
    size_bytes: int
    execution_time_ms: float


# ============================================================================
# IndicCacheManager (SQLite WAL Engine)
# ============================================================================

class IndicCacheManager:
    """Manages SQLite-backed content-addressed cache with WAL concurrency and LRU eviction."""

    _instance: IndicCacheManager | None = None

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self.enabled = os.getenv("BODHAN_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")

        # Configure storage directory
        if cache_dir is not None:
            self.cache_dir = Path(cache_dir).resolve()
        else:
            env_dir = os.getenv("BODHAN_CACHE_DIR")
            if env_dir:
                self.cache_dir = Path(env_dir).resolve()
            else:
                self.cache_dir = Path.home() / ".cache" / "bodhan"

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.cache_dir / "indic_cache.db"

        # Capacity and TTL limits
        self.max_bytes = int(os.getenv("BODHAN_CACHE_MAX_MB", "500")) * 1024 * 1024
        self.max_entries = int(os.getenv("BODHAN_CACHE_MAX_ENTRIES", "10000"))
        ttl_hours = float(os.getenv("BODHAN_CACHE_TTL_HOURS", "168.0"))
        self.ttl_seconds = ttl_hours * 3600.0 if ttl_hours > 0 else 0.0

        if self.enabled:
            self._init_db()

    @classmethod
    def get_instance(cls, cache_dir: Path | str | None = None) -> IndicCacheManager:
        if cls._instance is None:
            cls._instance = IndicCacheManager(cache_dir=cache_dir)
        elif cache_dir is not None and cls._instance.cache_dir != Path(cache_dir).resolve():
            cls._instance = IndicCacheManager(cache_dir=cache_dir)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    @contextlib.contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=5.0,
            check_same_thread=False,
        )
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        try:
            with self._get_connection() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS indic_cache (
                        key TEXT PRIMARY KEY,
                        tool_name TEXT NOT NULL,
                        input_summary TEXT NOT NULL,
                        response_data TEXT NOT NULL,
                        response_blob BLOB,
                        created_at REAL NOT NULL,
                        last_accessed_at REAL NOT NULL,
                        access_count INTEGER NOT NULL DEFAULT 1,
                        size_bytes INTEGER NOT NULL,
                        execution_time_ms REAL NOT NULL DEFAULT 0.0
                    );

                    CREATE INDEX IF NOT EXISTS idx_indic_cache_lru 
                    ON indic_cache (last_accessed_at ASC);

                    CREATE INDEX IF NOT EXISTS idx_indic_cache_tool 
                    ON indic_cache (tool_name);

                    CREATE TABLE IF NOT EXISTS indic_cache_stats (
                        stat_name TEXT PRIMARY KEY,
                        stat_value REAL NOT NULL DEFAULT 0
                    );

                    INSERT OR IGNORE INTO indic_cache_stats (stat_name, stat_value) VALUES 
                    ('hits', 0), ('misses', 0), ('bypasses', 0), ('bytes_saved', 0), ('latency_saved_ms', 0);
                """)
        except Exception as e:
            logger.error("Failed to initialize SQLite cache database: %s", e)
            self.enabled = False

    def get(self, key: str) -> CacheEntry | None:
        if not self.enabled:
            return None

        now = time.time()
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM indic_cache WHERE key = ?", (key,)
                ).fetchone()

                if not row:
                    conn.execute(
                        "UPDATE indic_cache_stats SET stat_value = stat_value + 1 WHERE stat_name = 'misses'"
                    )
                    return None

                # TTL check
                created_at = float(row["created_at"])
                if self.ttl_seconds > 0 and (now - created_at) > self.ttl_seconds:
                    conn.execute("DELETE FROM indic_cache WHERE key = ?", (key,))
                    conn.execute(
                        "UPDATE indic_cache_stats SET stat_value = stat_value + 1 WHERE stat_name = 'misses'"
                    )
                    return None

                # Update access stats
                new_count = int(row["access_count"]) + 1
                conn.execute(
                    """UPDATE indic_cache 
                       SET last_accessed_at = ?, access_count = access_count + 1 
                       WHERE key = ?""",
                    (now, key),
                )
                conn.execute(
                    "UPDATE indic_cache_stats SET stat_value = stat_value + 1 WHERE stat_name = 'hits'"
                )
                conn.execute(
                    "UPDATE indic_cache_stats SET stat_value = stat_value + ? WHERE stat_name = 'bytes_saved'",
                    (row["size_bytes"],),
                )
                conn.execute(
                    "UPDATE indic_cache_stats SET stat_value = stat_value + ? WHERE stat_name = 'latency_saved_ms'",
                    (row["execution_time_ms"],),
                )

                return CacheEntry(
                    key=str(row["key"]),
                    tool_name=str(row["tool_name"]),
                    input_summary=str(row["input_summary"]),
                    response_data=str(row["response_data"]),
                    response_blob=bytes(row["response_blob"]) if row["response_blob"] is not None else None,
                    created_at=created_at,
                    last_accessed_at=now,
                    access_count=new_count,
                    size_bytes=int(row["size_bytes"]),
                    execution_time_ms=float(row["execution_time_ms"]),
                )
        except Exception as e:
            logger.warning("Cache read error for key %s: %s", key[:12] if key else "", e)
            return None

    def record_bypass(self) -> None:
        if not self.enabled:
            return
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE indic_cache_stats SET stat_value = stat_value + 1 WHERE stat_name = 'bypasses'"
                )
        except Exception as e:
            logger.warning("Cache record_bypass error: %s", e)

    def put(
        self,
        key: str,
        tool_name: str,
        input_summary: str,
        response_data: str,
        response_blob: bytes | None = None,
        execution_time_ms: float = 0.0,
    ) -> None:
        if not self.enabled:
            return

        now = time.time()
        size_bytes = len(response_data.encode("utf-8")) + (len(response_blob) if response_blob else 0)

        try:
            with self._get_connection() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO indic_cache 
                       (key, tool_name, input_summary, response_data, response_blob, 
                        created_at, last_accessed_at, access_count, size_bytes, execution_time_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        key,
                        tool_name,
                        input_summary[:120],
                        response_data,
                        response_blob,
                        now,
                        now,
                        size_bytes,
                        execution_time_ms,
                    ),
                )
            self._enforce_capacity()
        except Exception as e:
            logger.warning("Cache write error for key %s: %s", key[:12] if key else "", e)

    # Convenience alias for notebook and dict-like compatibility
    set = put

    def _enforce_capacity(self) -> None:
        """Evict oldest entries by last_accessed_at if limits are exceeded.
        
        Hysteresis:
        High watermark (100%): triggers eviction when count > max_entries OR total_sz > max_bytes.
        Low watermark (85%): prunes entries until count <= 85% and total_sz <= 85%.
        """
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt, SUM(size_bytes) AS total_sz FROM indic_cache"
                ).fetchone()
                count = int(row["cnt"] or 0)
                total_sz = int(row["total_sz"] or 0)

                if count > self.max_entries or total_sz > self.max_bytes:
                    target_entries = int(self.max_entries * 0.85)
                    target_bytes = int(self.max_bytes * 0.85)

                    rows = conn.execute(
                        "SELECT key, size_bytes FROM indic_cache ORDER BY last_accessed_at ASC"
                    ).fetchall()

                    keys_to_evict: list[str] = []
                    cur_count = count
                    cur_sz = total_sz

                    for r in rows:
                        if cur_count <= target_entries and cur_sz <= target_bytes:
                            break
                        keys_to_evict.append(r["key"])
                        cur_count -= 1
                        cur_sz -= int(r["size_bytes"] or 0)

                    if keys_to_evict:
                        reclaimed_bytes = total_sz - cur_sz
                        logger.info(
                            "[CACHE EVICT] Limits exceeded (count=%d, size=%.1fMB). Evicting %d entries (reclaimed %.1fMB) to reach low watermark 85%%",
                            count,
                            total_sz / (1024 * 1024),
                            len(keys_to_evict),
                            reclaimed_bytes / (1024 * 1024),
                        )
                        for i in range(0, len(keys_to_evict), 500):
                            chunk = keys_to_evict[i : i + 500]
                            placeholders = ",".join("?" for _ in chunk)
                            conn.execute(
                                f"DELETE FROM indic_cache WHERE key IN ({placeholders})", chunk
                            )
        except Exception as e:
            logger.warning("Cache eviction error: %s", e)

    def get_stats(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}

        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt, SUM(size_bytes) AS total_sz FROM indic_cache"
            ).fetchone()
            total_entries = int(row["cnt"] or 0)
            total_bytes = int(row["total_sz"] or 0)

            stats_rows = conn.execute(
                "SELECT stat_name, stat_value FROM indic_cache_stats"
            ).fetchall()
            stats_dict = {r["stat_name"]: float(r["stat_value"]) for r in stats_rows}

            by_tool_rows = conn.execute(
                """SELECT tool_name, COUNT(*) as cnt, SUM(size_bytes) as sz 
                   FROM indic_cache GROUP BY tool_name"""
            ).fetchall()
            by_tool = {
                r["tool_name"]: {
                    "entries": int(r["cnt"]),
                    "size_bytes": int(r["sz"] or 0),
                    "size_kb": round((r["sz"] or 0) / 1024.0, 2),
                }
                for r in by_tool_rows
            }

        hits = int(stats_dict.get("hits", 0))
        misses = int(stats_dict.get("misses", 0))
        bypasses = int(stats_dict.get("bypasses", 0))
        total_calls = hits + misses
        ratio = (hits / total_calls * 100.0) if total_calls > 0 else 0.0

        return {
            "enabled": True,
            "db_path": str(self.db_path),
            "total_entries": total_entries,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            "total_size_bytes": total_bytes,
            "max_size_mb": round(self.max_bytes / (1024 * 1024), 2),
            "max_entries": self.max_entries,
            "total_hits": hits,
            "total_misses": misses,
            "total_bypasses": bypasses,
            "hit_ratio_percent": round(ratio, 2),
            "total_bytes_saved": int(stats_dict.get("bytes_saved", 0)),
            "total_latency_saved_sec": round(stats_dict.get("latency_saved_ms", 0.0) / 1000.0, 2),
            "by_tool": by_tool,
        }

    def clear(self, tool_name: str | None = None) -> int:
        if not self.enabled:
            return 0
        if tool_name:
            with self._get_connection() as conn:
                cur = conn.execute("DELETE FROM indic_cache WHERE tool_name = ?", (tool_name,))
                return cur.rowcount
        else:
            with self._get_connection() as conn:
                cur = conn.execute("DELETE FROM indic_cache")
                count = cur.rowcount
            # Execute VACUUM in autocommit mode outside of transaction
            try:
                conn_vac = sqlite3.connect(str(self.db_path), timeout=5.0)
                conn_vac.isolation_level = None
                conn_vac.execute("VACUUM")
                conn_vac.close()
            except Exception as e:
                logger.debug("VACUUM skipped: %s", e)
            return count


def get_cache_manager(cache_dir: Path | str | None = None) -> IndicCacheManager:
    """Return the global IndicCacheManager singleton."""
    return IndicCacheManager.get_instance(cache_dir=cache_dir)


# ============================================================================
# Caching Decorator
# ============================================================================

def indic_cache(
    tool_name: str,
    key_generator: Callable[..., str],
    is_binary_asset: bool = False,
):
    """Decorator for caching deterministic Indic MCP tools.
    
    Multi-process safe, zero-dependency, WAL-backed caching layer.
    Guarantees:
    - Never caches errors or exceptions (ToolError, HTTP errors, script failures).
    - Checks BODHAN_CACHE_ENABLED and bypass_cache parameter.
    - Accurately measures elapsed execution time on MISS.
    - Reconstitutes binary audio files (WAV) on HIT for synthesize_speech.
    - Restores file deliverables (transcribe_audio output_path) on HIT from
      captured file bytes when available, so destination names can differ
      between identical calls without a re-paid provider call.
    - All telemetry logged strictly to stderr via get_logger("mcp").
    """
    def decorator(fn: Callable[..., Coroutine[Any, Any, str]]):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            manager = IndicCacheManager.get_instance()

            # Bind arguments using the wrapped function's signature
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            call_args = bound.arguments

            bypass = bool(call_args.get("bypass_cache", False))

            if not manager.enabled:
                return await fn(*args, **kwargs)

            if bypass:
                manager.record_bypass()
                logger.info("[CACHE BYPASS] tool=%s", tool_name)
            else:
                try:
                    try:
                        cache_key = key_generator(**call_args)
                    except TypeError:
                        cache_key = key_generator(*args, **kwargs)
                except Exception as key_err:
                    logger.debug("Failed to generate cache key for %s: %s", tool_name, key_err)
                    cache_key = None

                if cache_key:
                    cached = manager.get(cache_key)
                    if cached:
                        logger.info(
                            "[CACHE HIT] tool=%s key=%s... latency_saved=%.0fms bytes=%d",
                            tool_name,
                            cache_key[:12],
                            cached.execution_time_ms,
                            cached.size_bytes,
                        )

                        # Special Handling for Binary Asset Restoration (synthesize_speech)
                        if is_binary_asset and cached.response_blob:
                            from src.bodhan_mcp.server import resolve_artifact_path
                            output_path = call_args.get("output_path") or f"speech_{call_args.get('language', 'out')}.wav"
                            out_file = resolve_artifact_path(output_path)
                            out_file.parent.mkdir(parents=True, exist_ok=True)

                            # Reconstitute WAV file if missing or size differs
                            if not out_file.is_file() or out_file.stat().st_size != len(cached.response_blob):
                                out_file.write_bytes(cached.response_blob)

                            try:
                                meta = inspect_audio(out_file)
                                dur = meta.duration_seconds
                                sr = meta.sample_rate
                            except Exception:
                                dur = 0.0
                                sr = 24000

                            return json.dumps({
                                "status": "success",
                                "output_file": str(out_file),
                                "duration_seconds": dur,
                                "sample_rate": sr,
                                "language": call_args.get("language"),
                                "cached": True,
                            })

                        # Non-binary tools with a file deliverable (e.g.
                        # transcribe_audio with output_path): honor the
                        # caller's requested destination on a cache HIT by
                        # materializing the cached payload to that path. The
                        # tool body does not run on a hit, so without this a
                        # caller asking for a different output_path would get
                        # a success envelope but no file at the requested path.
                        requested_output = call_args.get("output_path")
                        if requested_output:
                            try:
                                from src.bodhan_mcp.server import resolve_artifact_path
                                out_file = resolve_artifact_path(requested_output)
                                out_file.parent.mkdir(parents=True, exist_ok=True)
                                materialized = False
                                if cached.response_blob:
                                    if not out_file.is_file() or out_file.stat().st_size != len(cached.response_blob):
                                        out_file.write_bytes(cached.response_blob)
                                    materialized = True
                                else:
                                    try:
                                        envelope = json.loads(cached.response_data)
                                    except Exception:
                                        envelope = None
                                    if envelope is None:
                                        # Raw-text cached payload (stored without
                                        # an output_path): write it to the file.
                                        out_file.write_text(cached.response_data, encoding="utf-8")
                                        materialized = True
                                    elif isinstance(envelope, dict) and envelope.get("artifact_path"):
                                        src = Path(str(envelope["artifact_path"]))
                                        if src.is_file():
                                            shutil.copyfile(src, out_file)
                                            materialized = True
                                if materialized:
                                    try:
                                        data = json.loads(cached.response_data)
                                        if isinstance(data, dict) and "artifact_path" in data:
                                            data["artifact_path"] = str(out_file)
                                            data["cached"] = True
                                            return json.dumps(data, ensure_ascii=False)
                                    except Exception:
                                        pass
                                    return cached.response_data
                                logger.warning(
                                    "[CACHE HIT] tool=%s: cached entry predates file-byte capture; cannot materialize output_path '%s'",
                                    tool_name,
                                    requested_output,
                                )
                            except Exception as mat_err:
                                logger.warning(
                                    "[CACHE HIT] tool=%s: failed to materialize output_path '%s': %s",
                                    tool_name,
                                    requested_output,
                                    mat_err,
                                )

                        return cached.response_data

                logger.info("[CACHE MISS] tool=%s", tool_name)

            # MISS or BYPASS: execute the wrapped function
            start_time = time.perf_counter()
            result = await fn(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            # Store successful result in cache (ToolError/exceptions never reach here)
            try:
                try:
                    cache_key = key_generator(**call_args)
                except TypeError:
                    cache_key = key_generator(*args, **kwargs)

                if cache_key:
                    blob: bytes | None = None
                    if is_binary_asset or call_args.get("output_path"):
                        try:
                            from src.bodhan_mcp.server import resolve_artifact_path
                            output_path = call_args.get("output_path") or f"speech_{call_args.get('language', 'out')}.wav"
                            out_file = resolve_artifact_path(output_path)
                            if out_file.is_file():
                                blob = out_file.read_bytes()
                        except Exception as blob_err:
                            logger.debug("Could not capture output file bytes for cache entry: %s", blob_err)

                    summary = str(call_args.get("text") or call_args.get("file_path") or "")[:80]
                    manager.put(
                        key=cache_key,
                        tool_name=tool_name,
                        input_summary=summary,
                        response_data=result,
                        response_blob=blob,
                        execution_time_ms=elapsed_ms,
                    )
                    logger.info(
                        "[CACHE STORE] tool=%s key=%s... duration=%.0fms size=%dB",
                        tool_name,
                        cache_key[:12],
                        elapsed_ms,
                        len(result.encode("utf-8")) + (len(blob) if blob else 0),
                    )
            except Exception as store_err:
                logger.debug("Failed to store cache entry for %s: %s", tool_name, store_err)

            return result

        return wrapper
    return decorator
