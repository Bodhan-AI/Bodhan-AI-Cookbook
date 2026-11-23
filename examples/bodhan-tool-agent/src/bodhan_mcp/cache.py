"""Content-hash cache, pricing, and the cost ledger."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from .models import ErrorCode, ToolError

log = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = "1"

INR_PER_AUDIO_MINUTE = 0.10
INR_PER_10K_OUTPUT_TOKENS = 0.20
INR_PER_10K_SPEAK_CHARS = 6.00
INR_PER_IMAGE = 0.20
CHARS_PER_TOKEN_ESTIMATE = 2.0


def _inr(x: float) -> float:
    return round(x, 6)


def cost_transcribe(audio_seconds: float) -> float:
    return _inr(max(audio_seconds, 0.0) / 60.0 * INR_PER_AUDIO_MINUTE)


def cost_translate(output_tokens: float) -> float:
    return _inr(max(output_tokens, 0.0) / 10_000 * INR_PER_10K_OUTPUT_TOKENS)


def cost_speak(chars: int) -> float:
    return _inr(max(chars, 0) / 10_000 * INR_PER_10K_SPEAK_CHARS)


def cost_ocr(images: int) -> float:
    return _inr(max(images, 0) * INR_PER_IMAGE)


def estimate_output_tokens(output_chars: int) -> int:
    return math.ceil(max(output_chars, 0) / CHARS_PER_TOKEN_ESTIMATE)


def price_table() -> dict[str, dict[str, Any]]:
    return {
        "transcribe": {"unit": "input audio minute", "inr": INR_PER_AUDIO_MINUTE},
        "translate": {"unit": "10,000 output tokens", "inr": INR_PER_10K_OUTPUT_TOKENS},
        "speak": {"unit": "10,000 input characters", "inr": INR_PER_10K_SPEAK_CHARS},
        "ocr": {"unit": "image (one per PDF page)", "inr": INR_PER_IMAGE},
    }


_HASH_BLOCK = 1024 * 1024


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    return hash_bytes(unicodedata.normalize("NFC", text).encode("utf-8"))


_file_memo: dict[tuple[str, int, int], str] = {}
_file_memo_lock = threading.Lock()


def hash_file(path: str | os.PathLike[str]) -> str:
    p = Path(path)
    st = p.stat()
    memo_key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    with _file_memo_lock:
        hit = _file_memo.get(memo_key)
    if hit:
        return hit
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(_HASH_BLOCK), b""):
            h.update(block)
    digest = h.hexdigest()
    with _file_memo_lock:
        _file_memo[memo_key] = digest
    return digest


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_key(
    model: str,
    op: str,
    params: Mapping[str, Any],
    inputs: Sequence[str] = (),
) -> str:
    return hash_text(
        canonical_json(
            {
                "v": CACHE_SCHEMA_VERSION,
                "model": model,
                "op": op,
                "params": params,
                "inputs": list(inputs),
            }
        )
    )


def compute_content_hash(data: bytes) -> str:
    return hash_bytes(data)


@dataclass
class ComputeResult:
    value: dict[str, Any]
    blobs: dict[str, bytes] = field(default_factory=dict)
    cost_inr: float = 0.0


@dataclass(frozen=True)
class CacheEntry:
    key: str
    model: str
    value: dict[str, Any]
    blobs: Mapping[str, Path]
    cost_inr: float
    created_at: float


class CacheResult(NamedTuple):
    entry: CacheEntry
    cached: bool

    @property
    def charged_inr(self) -> float:
        return 0.0 if self.cached else self.entry.cost_inr

    @property
    def avoided_inr(self) -> float:
        return self.entry.cost_inr if self.cached else 0.0


_BLOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_MODEL_NAME = re.compile(r"^[a-z0-9_-]{1,32}$")
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_ENTRY_FILE = "entry.json"


class _SyncInflightSlot:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.entry: CacheEntry | None = None
        self.error: BaseException | None = None


class DiskCache:
    """Directory-per-entry cache with async and sync single-flight deduplication."""

    def __init__(self, root: str | os.PathLike[str] | None = None, *, refresh: bool = False):
        default = Path.home() / ".cache" / "bodhan-tool-agent"
        self.root = Path(root).expanduser() if root else default
        self.refresh = refresh
        self._session_keys: set[tuple[str, str]] = set()
        self._inflight: dict[tuple[str, str], asyncio.Future[CacheEntry]] = {}
        self._sync_lock = threading.Lock()
        self._sync_inflight: dict[tuple[str, str], _SyncInflightSlot] = {}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DiskCache:
        env = os.environ if env is None else env
        refresh = (env.get("BODHAN_CACHE_REFRESH") or "").strip().lower() in {"1", "true", "yes"}
        return cls(env.get("BODHAN_CACHE_DIR") or None, refresh=refresh)

    def _dir(self, model: str, key: str) -> Path:
        if not _MODEL_NAME.match(model) or not _KEY_RE.match(key):
            raise ValueError("invalid cache model or key")
        return self.root / model / key[:2] / key

    def get(self, model: str, key: str, *, bypass: bool = False) -> CacheEntry | None:
        if bypass:
            return None
        if self.refresh and (model, key) not in self._session_keys:
            return None
        d = self._dir(model, key)
        meta_path = d / _ENTRY_FILE
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            blobs = {name: d / name for name in meta["blobs"]}
            for p in blobs.values():
                if not p.is_file():
                    raise FileNotFoundError(p.name)
            return CacheEntry(
                key=key,
                model=model,
                value=meta["value"],
                blobs=blobs,
                cost_inr=float(meta["cost_inr"]),
                created_at=float(meta["created_at"]),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Dropping corrupt cache entry %s/%s: %s", model, key[:12], exc)
            shutil.rmtree(d, ignore_errors=True)
            return None

    def put(self, model: str, key: str, result: ComputeResult) -> CacheEntry:
        final = self._dir(model, key)
        for name in result.blobs:
            if not _BLOB_NAME.match(name) or name == _ENTRY_FILE:
                raise ValueError(f"invalid blob name {name!r}")
        created_at = time.time()
        meta = {
            "value": result.value,
            "blobs": sorted(result.blobs),
            "cost_inr": _inr(result.cost_inr),
            "created_at": created_at,
            "schema": CACHE_SCHEMA_VERSION,
        }
        tmp_root = self.root / ".tmp"
        tmp = tmp_root / uuid.uuid4().hex
        tmp.mkdir(parents=True)
        try:
            for name, data in result.blobs.items():
                (tmp / name).write_bytes(data)
            (tmp / _ENTRY_FILE).write_text(
                json.dumps(meta, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            final.parent.mkdir(parents=True, exist_ok=True)
            if (self.refresh or True) and final.exists():
                shutil.rmtree(final, ignore_errors=True)
            try:
                os.replace(tmp, final)
            except OSError:
                if not (final / _ENTRY_FILE).is_file():
                    raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self._session_keys.add((model, key))
        entry = self.get(model, key)
        if entry is None:  # pragma: no cover
            raise ToolError(ErrorCode.INTERNAL, "Cache write could not be read back.")
        return entry

    def get_or_compute_sync(
        self,
        model: str,
        key: str,
        compute: Callable[[], ComputeResult],
        *,
        bypass: bool = False,
    ) -> CacheResult:
        """Synchronous cache lookup/write with thread-level single-flight dedupe."""
        slot_key = (model, key)
        with self._sync_lock:
            entry = self.get(model, key, bypass=bypass)
            if entry is not None:
                return CacheResult(entry, True)
            existing_slot = self._sync_inflight.get(slot_key)
            if existing_slot is None:
                slot = _SyncInflightSlot()
                self._sync_inflight[slot_key] = slot
                owner = True
            else:
                slot = existing_slot
                owner = False

        if not owner:
            slot.event.wait()
            if slot.error is not None:
                raise slot.error
            assert slot.entry is not None
            return CacheResult(slot.entry, True)

        try:
            result = compute()
            entry = self.put(model, key, result)
            slot.entry = entry
            return CacheResult(entry, False)
        except BaseException as exc:
            slot.error = exc
            raise
        finally:
            with self._sync_lock:
                self._sync_inflight.pop(slot_key, None)
                slot.event.set()

    async def get_or_compute(
        self,
        model: str,
        key: str,
        compute: Callable[[], Awaitable[ComputeResult]],
    ) -> CacheResult:
        entry = self.get(model, key)
        if entry is not None:
            return CacheResult(entry, True)

        slot = (model, key)
        pending = self._inflight.get(slot)
        if pending is not None:
            return CacheResult(await asyncio.shield(pending), True)

        fut: asyncio.Future[CacheEntry] = asyncio.get_running_loop().create_future()
        self._inflight[slot] = fut
        try:
            result = await compute()
            entry = await asyncio.to_thread(self.put, model, key, result)
        except asyncio.CancelledError:
            fut.set_exception(
                ToolError(
                    ErrorCode.INTERNAL,
                    "The in-flight call this request was waiting on was cancelled.",
                    retryable=True,
                    hint="Retry the call.",
                )
            )
            fut.exception()
            raise
        except Exception as exc:
            fut.set_exception(exc)
            fut.exception()
            raise
        else:
            fut.set_result(entry)
            return CacheResult(entry, False)
        finally:
            self._inflight.pop(slot, None)


@dataclass(frozen=True)
class Charge:
    ts: float
    model: str
    op: str
    cost_inr: float
    avoided_inr: float
    cached: bool
    latency_ms: int
    units: Mapping[str, float]


class CostLedger:
    """Running record of spend. Thread-safe. Optionally mirrors each charge to a JSONL file."""

    def __init__(self, jsonl_path: str | os.PathLike[str] | None = None) -> None:
        self._charges: list[Charge] = []
        self._lock = threading.Lock()
        self._jsonl = Path(jsonl_path).expanduser() if jsonl_path else None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CostLedger:
        env = os.environ if env is None else env
        return cls(env.get("BODHAN_LEDGER_PATH") or None)

    def record(
        self,
        model: str,
        op: str,
        *,
        cost_inr: float,
        cached: bool = False,
        avoided_inr: float = 0.0,
        latency_ms: int = 0,
        units: Mapping[str, float] | None = None,
    ) -> Charge:
        charge = Charge(
            ts=time.time(),
            model=model,
            op=op,
            cost_inr=0.0 if cached else _inr(cost_inr),
            avoided_inr=_inr(avoided_inr if avoided_inr > 0 else (cost_inr if cached else 0.0)),
            cached=cached,
            latency_ms=latency_ms,
            units=dict(units or {}),
        )
        with self._lock:
            self._charges.append(charge)
            if self._jsonl is not None:
                self._append_jsonl(charge)
        return charge

    def _append_jsonl(self, charge: Charge) -> None:
        try:
            self._jsonl.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            line = json.dumps({**charge.__dict__, "units": dict(charge.units)}, ensure_ascii=False)
            with self._jsonl.open("a", encoding="utf-8") as f:  # type: ignore[union-attr]
                f.write(line + "\n")
        except OSError as exc:
            log.warning("Could not append to ledger %s: %s", self._jsonl, exc)

    @property
    def total_inr(self) -> float:
        with self._lock:
            return _inr(sum(c.cost_inr for c in self._charges))

    @property
    def avoided_inr(self) -> float:
        with self._lock:
            return _inr(sum(c.avoided_inr for c in self._charges))

    def by_model(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        with self._lock:
            for c in self._charges:
                row = out.setdefault(
                    c.model, {"calls": 0, "cache_hits": 0, "cost_inr": 0.0, "avoided_inr": 0.0}
                )
                row["calls"] += 1
                row["cache_hits"] += int(c.cached)
                row["cost_inr"] = _inr(row["cost_inr"] + c.cost_inr)
                row["avoided_inr"] = _inr(row["avoided_inr"] + c.avoided_inr)
        return out

    def charges(self) -> Iterable[Charge]:
        with self._lock:
            return list(self._charges)

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_inr": self.total_inr,
            "avoided_by_cache_inr": self.avoided_inr,
            "by_model": self.by_model(),
            "calls": len(self._charges),
        }