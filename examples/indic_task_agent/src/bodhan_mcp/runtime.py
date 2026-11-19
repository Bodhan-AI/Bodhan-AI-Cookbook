"""Per-run state shared by every tool: cost ledger, cache, artifact registry, trace hook.

The MCP server creates one RunContext per server process; the agent creates one
per task run. Tools never touch globals, which keeps tests deterministic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import errors as E

# Hosted prices from the assignment brief / Bodhan console (INR).
PRICE_TRANSCRIBE_PER_MIN = 0.10
PRICE_TRANSLATE_PER_10K_OUT_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_OCR_PER_IMAGE = 0.20


def price_transcribe(seconds: float) -> float:
    return math.ceil(max(seconds, 0.0)) / 60.0 * PRICE_TRANSCRIBE_PER_MIN


def price_translate(output_tokens: int) -> float:
    return max(output_tokens, 0) / 10_000 * PRICE_TRANSLATE_PER_10K_OUT_TOKENS


def price_speak(chars: int) -> float:
    return max(chars, 0) / 10_000 * PRICE_SPEAK_PER_10K_CHARS


def price_ocr(images: int = 1) -> float:
    return images * PRICE_OCR_PER_IMAGE


# Per-tool-call cost accumulator. `registry.invoke` sets a fresh list; the client
# appends every amount it commits, so concurrent tool calls report their own cost.
COST_SCOPE: ContextVar[list[float] | None] = ContextVar("bodhan_cost_scope", default=None)


RUN_DEADLINE: ContextVar[float | None] = ContextVar("bodhan_run_deadline", default=None)  # time.monotonic() value


def deadline_left() -> float | None:
    """Seconds until the run deadline (None when no deadline is set, e.g. the MCP server)."""
    d = RUN_DEADLINE.get()
    return None if d is None else d - time.monotonic()


def _scope_add(amount: float) -> None:
    scope = COST_SCOPE.get()
    if scope is not None:
        scope.append(amount)


async def settle(*aws):
    """Like asyncio.gather, but lets every sibling finish before raising the first error,
    so no paid request is still running (and missing from the cost report) when a tool
    returns."""
    results = await asyncio.gather(*aws, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return results


# ---------------------------------------------------------------------------
# Cost ledger with reservations
# ---------------------------------------------------------------------------

@dataclass
class Reservation:
    id: int
    model: str
    amount: float
    purpose: str
    open: bool = True


@dataclass
class CostLedger:
    """Tracks Bodhan spend. A call must reserve its worst-case cost *before* it is
    sent, so concurrent calls can never push the total over `max_inr`.

    * billed     - requests that returned success (cost computed from the response)
    * uncertain  - requests that may have been billed but gave no usable answer
                   (timeouts, 5xx, dropped connections). Counted against the budget.
    * reserved   - in-flight worst-case reservations.
    """

    max_inr: float | None = None
    billed: float = 0.0
    uncertain: float = 0.0
    reserved: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)
    calls: int = 0
    cache_hits: int = 0
    _next: int = 0
    # LLM accounting (only priced when the user supplies a rate).
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_calls: int = 0
    llm_cost: float = 0.0
    llm_priced: bool = False
    llm_over_bound: float = 0.0  # planner spend beyond its pre-call bound (expected 0; reported when not)

    @property
    def committed(self) -> float:
        return self.billed + self.uncertain + self.llm_cost

    @property
    def remaining(self) -> float | None:
        if self.max_inr is None:
            return None
        return self.max_inr - self.committed - self.reserved

    def can_afford(self, amount: float) -> bool:
        return self.max_inr is None or self.committed + self.reserved + amount <= self.max_inr + 1e-9

    def reserve(self, model: str, amount: float, purpose: str) -> Reservation:
        if not self.can_afford(amount):
            raise E.ToolError(
                E.BUDGET_EXCEEDED,
                f"Budget would be exceeded: {purpose} needs up to ₹{amount:.4f} but only ₹{max(self.remaining or 0, 0):.4f} "
                f"of ₹{self.max_inr:.2f} remains.",
                hint="Stop and report partial results, or use a smaller input (fewer pages, shorter text).",
                details={"needed_inr": round(amount, 4), "remaining_inr": round(max(self.remaining or 0, 0), 4),
                         "max_inr": self.max_inr},
            )
        self._next += 1
        self.reserved += amount
        return Reservation(self._next, model, amount, purpose)

    def _close(self, r: Reservation) -> None:
        if r.open:
            self.reserved -= r.amount
            r.open = False

    def commit(self, r: Reservation, actual: float) -> None:
        self._close(r)
        self.billed += actual
        self.by_model[r.model] = self.by_model.get(r.model, 0.0) + actual
        self.calls += 1
        _scope_add(actual)

    def mark_uncertain(self, r: Reservation) -> None:
        self._close(r)
        self.uncertain += r.amount
        self.by_model[r.model] = self.by_model.get(r.model, 0.0) + r.amount
        _scope_add(r.amount)

    def release(self, r: Reservation) -> None:
        self._close(r)

    def add_llm(self, prompt_tokens: int, completion_tokens: int, cost: float | None, calls: int = 1) -> None:
        """`calls=0` charges planner attempts that failed after sending (no reply, possibly billed)."""
        self.llm_calls += calls
        self.llm_prompt_tokens += prompt_tokens
        self.llm_completion_tokens += completion_tokens
        if cost is not None:
            self.llm_cost += cost
            self.llm_priced = True

    _STATE_FIELDS = ("billed", "uncertain", "by_model", "calls", "cache_hits", "llm_prompt_tokens", "llm_completion_tokens",
                     "llm_calls", "llm_cost", "llm_priced", "llm_over_bound")

    def to_state(self) -> dict[str, Any]:
        """Settled amounts only: in-flight reservations are not saved (a checkpoint is written
        between turns, when nothing is in flight)."""
        return {k: getattr(self, k) for k in self._STATE_FIELDS}

    def load_state(self, state: dict[str, Any]) -> None:
        for k in self._STATE_FIELDS:
            if k in state:
                setattr(self, k, dict(state[k]) if k == "by_model" else state[k])

    def summary(self) -> dict[str, Any]:
        return {
            "max_inr": self.max_inr,
            "bodhan_billed_inr": round(self.billed, 4),
            "bodhan_uncertain_inr": round(self.uncertain, 4),
            "llm_inr": round(self.llm_cost, 4) if self.llm_priced else None,
            "total_counted_inr": round(self.committed, 4),
            "by_model_inr": {k: round(v, 4) for k, v in sorted(self.by_model.items())},
            "bodhan_calls": self.calls,
            "cache_hits": self.cache_hits,
            **({"llm_over_pre_call_bound_inr": round(self.llm_over_bound, 4)} if self.llm_over_bound > 0 else {}),
            "llm": {"calls": self.llm_calls, "prompt_tokens": self.llm_prompt_tokens,
                    "completion_tokens": self.llm_completion_tokens,
                    "pricing": "priced from CLI rates" if self.llm_priced else "not priced (no rate supplied); tokens reported only"},
        }


# ---------------------------------------------------------------------------
# Content-addressed cache of model outputs
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_key(model: str, payload_hash: str, params: dict[str, Any]) -> str:
    blob = json.dumps({"model": model, "input": payload_hash, "params": params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


CACHE_VERSION = 2


class ResultCache:
    """Cache keyed by (model, exact input bytes, settings).

    * In memory, every successful response is kept for the run, so an identical
      request is never sent twice in one run (no waste).
    * On disk, only *complete* results are stored (not truncated translations or OCR),
      so a later run can recover once the upstream behaves. Entries are versioned,
      carry the SHA-256 of their binary, and are published atomically (binary first,
      manifest last via os.replace). Anything unreadable or inconsistent is a miss.
    * `directory` is the shared cross-run cache (optional); `local` is the run folder's
      own store, which makes --resume free of re-billing even without the shared cache.
    * A directory that cannot be used is skipped with a warning (memory still works).
    """

    def __init__(self, directory: Path | None, local: Path | None = None):
        self.warning: str | None = None
        self.dirs: list[Path] = []
        for d in (local, directory):
            if d is None:
                continue
            try:
                d.mkdir(parents=True, exist_ok=True)
                probe = d / f".write_test_{os.getpid()}"
                probe.write_bytes(b"ok")
                probe.unlink()
                self.dirs.append(d)
            except OSError as exc:
                self.warning = f"cache folder {d} is not usable ({type(exc).__name__}); continuing without it"
        self.memory: dict[str, tuple[dict, bytes | None]] = {}
        self.seen_this_run: set[str] = set()

    @property
    def dir(self) -> Path | None:  # first usable directory (kept for callers that only need one)
        return self.dirs[0] if self.dirs else None

    def get(self, key: str) -> tuple[dict, bytes | None, str] | None:
        if key in self.memory:
            meta, blob = self.memory[key]
            return meta, blob, "this_run"
        for d in self.dirs:
            entry = self._read(d, key)
            if entry is not None:
                self.memory[key] = entry
                return entry[0], entry[1], "previous_run"
        return None

    @staticmethod
    def _read(d: Path, key: str) -> tuple[dict, bytes | None] | None:
        try:
            entry = json.loads((d / f"{key}.json").read_text(encoding="utf-8"))
            if isinstance(entry, dict) and "v" not in entry:
                return ResultCache._read_legacy(d, key, entry)
            if not (isinstance(entry, dict) and entry.get("v") == CACHE_VERSION and entry.get("key") == key and isinstance(entry.get("meta"), dict)):
                return None
            blob = None
            if entry.get("blob_sha256"):
                blob = (d / f"{key}.bin").read_bytes()
                if sha256_bytes(blob) != entry["blob_sha256"]:
                    return None
        except (OSError, ValueError, UnicodeDecodeError):  # missing, partial or corrupt entry: a miss, refetch
            return None
        return entry["meta"], blob

    @staticmethod
    def _read_legacy(d: Path, key: str, meta: dict) -> tuple[dict, bytes | None] | None:
        """Entries written before CACHE_VERSION 2 (bare metadata, no checksum). They are used only
        if they are clearly complete results: truncated translations/OCR and audio entries without
        their WAV/MP3 bytes are misses and get refetched."""
        if meta.get("truncated") or meta.get("finish_reason") == "length":
            return None
        blob = None
        if "format" in meta:  # speech: the audio bytes must be present and look like audio
            blob = (d / f"{key}.bin").read_bytes()
            if not (blob[:4] == b"RIFF" or blob[:3] == b"ID3"):
                return None
        return meta, blob

    def put(self, key: str, meta: dict, blob: bytes | None = None, *, durable: bool = True) -> None:
        self.memory[key] = (meta, blob)
        self.seen_this_run.add(key)
        if not durable:
            return
        for d in self.dirs:
            tag = f".tmp{os.getpid()}_{id(meta)}"
            try:
                if blob is not None:
                    tmp = d / f"{key}.bin{tag}"
                    tmp.write_bytes(blob)
                    os.replace(tmp, d / f"{key}.bin")
                entry = {"v": CACHE_VERSION, "key": key, "meta": meta, "blob_sha256": sha256_bytes(blob) if blob is not None else None}
                tmp = d / f"{key}.json{tag}"
                tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, d / f"{key}.json")  # manifest last: readers never see a half-written entry
            except OSError:
                continue


# ---------------------------------------------------------------------------
# Run context
# ---------------------------------------------------------------------------

TraceFn = Callable[[dict[str, Any]], None]


@dataclass
class Artifact:
    path: Path
    kind: str  # transcript | translation | audio | subtitles | ocr | text | json
    producer: str  # tool name
    language: str | None = None
    provenance: str = ""  # which Bodhan model produced the content, or "llm-english", "code"
    verification: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)  # artifacts embedded in / derived from this one
    # What the artifact was made from, fixed at creation: later checks must use the same source.
    source_digest: str | None = None
    source_language: str | None = None
    digest: str | None = None  # SHA-256 of the file as written
    history: list[dict[str, Any]] = field(default_factory=list)  # every verification ever recorded, in order
    origin: list[str] = field(default_factory=list)  # names of the input files this artifact derives from

    def __post_init__(self) -> None:
        if self.digest is None and self.path.is_file():
            self.digest = sha256_bytes(self.path.read_bytes())
        if self.verification is not None:
            self.history.append(self.verification)

    def record_verification(self, verification: dict[str, Any]) -> dict[str, Any]:
        """Append a check. The effective status never improves on an earlier one: an
        artifact that failed once stays failed (make a new artifact to repair it)."""
        order = {None: 0, "pass": 1, "warn": 2, "fail": 3}
        self.history.append(verification)
        if order.get(verification.get("status"), 0) >= order.get((self.verification or {}).get("status"), 0):
            self.verification = verification
        return self.verification or verification

    def to_state(self) -> dict[str, Any]:
        return {"path": str(self.path), "kind": self.kind, "producer": self.producer, "language": self.language,
                "provenance": self.provenance, "verification": self.verification, "notes": self.notes, "depends_on": self.depends_on,
                "source_digest": self.source_digest, "source_language": self.source_language, "digest": self.digest, "history": self.history,
                "origin": self.origin}

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> Artifact:
        art = cls(Path(state["path"]), state["kind"], state["producer"], state.get("language"), state.get("provenance", ""),
                  None, list(state.get("notes") or []), list(state.get("depends_on") or []), state.get("source_digest"),
                  state.get("source_language"), state.get("digest"), [])
        art.verification = state.get("verification")
        art.history = list(state.get("history") or [])
        art.origin = list(state.get("origin") or [])
        return art

    def as_dict(self, base: Path | None = None) -> dict[str, Any]:
        p = str(self.path.relative_to(base)) if base and self.path.is_relative_to(base) else str(self.path)
        return {"path": p.replace("\\", "/"), "kind": self.kind, "producer": self.producer, "language": self.language,
                "provenance": self.provenance, "verification": self.verification, "notes": self.notes,
                "embeds": [Path(d).name for d in self.depends_on], "sha256": self.digest,
                "source_language": self.source_language, "verification_checks": len(self.history), "origin": self.origin}


@dataclass
class RunContext:
    artifacts_dir: Path
    ledger: CostLedger
    cache: ResultCache
    client: Any = None  # BodhanClient (typed loosely to avoid an import cycle)
    read_roots: list[Path] | None = None  # None = any readable path (standalone server default)
    trace: TraceFn | None = None
    verify_by_default: bool = True
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    transcripts: dict[str, Path] = field(default_factory=dict)  # source audio path -> segments JSON made this run
    # path -> (sha256 of the content read, character spans returned by read_text), so write_text can
    # accept a file as a summary's source only after the caller has read all of its current content
    read_spans: dict[str, tuple[str, list[tuple[int, int]]]] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)
    # Agent mode: Indic text the LLM is allowed to pass inline (task text + tool outputs).
    provenance_guard: Any = None

    def emit(self, event: dict[str, Any]) -> None:
        if self.trace:
            self.trace(event)

    # -- paths ---------------------------------------------------------------
    def resolve_input(self, raw: str) -> Path:
        if not raw or not str(raw).strip():
            raise E.ToolError(E.INVALID_ARGUMENT, "A file path is required.", hint="Pass the path of an input file or an artifact returned by an earlier tool.")
        p = Path(str(raw).strip().strip('"')).expanduser()
        if not p.is_absolute():
            # relative paths resolve against the artifacts dir first, then cwd
            cand = self.artifacts_dir / p
            p = cand if cand.exists() else Path.cwd() / p
        p = p.resolve()
        if self.read_roots is not None:
            roots = [r.resolve() for r in self.read_roots] + [self.artifacts_dir.resolve()]
            if not any(p == r or p.is_relative_to(r) for r in roots):
                raise E.ToolError(E.PATH_NOT_ALLOWED, f"Path is outside the allowed input folders: {raw}",
                                  hint="Use one of the task's input files or an artifact path returned by a tool.")
        if not p.exists():
            raise E.ToolError(E.FILE_NOT_FOUND, f"File not found: {raw}", hint="Check the path; list inputs with inspect_files.")
        return p

    def artifact_path(self, name: str, suffix: str) -> Path:
        """A fresh path inside artifacts_dir. `name` is sanitised; never escapes the dir."""
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "output").stem).strip("._") or "output"
        stem = stem[:80]
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        candidate = self.artifacts_dir / f"{stem}{suffix}"
        i = 2
        while candidate.exists() or str(candidate) in self.artifacts:
            candidate = self.artifacts_dir / f"{stem}_{i}{suffix}"
            i += 1
        return candidate

    def origin_of(self, label: str) -> list[str]:
        """Input files behind a path: an artifact's own origin, or the file name of an input."""
        if label in self.artifacts:
            return list(self.artifacts[label].origin)
        if not label or label == "inline":
            return []
        return [Path(label).name]

    def note_read(self, path: Path, content: str, start: int, end: int) -> None:
        digest = sha256_bytes(content.encode("utf-8"))
        known, spans = self.read_spans.get(str(path), (digest, []))
        self.read_spans[str(path)] = (digest, (spans if known == digest else []) + [(start, end)])

    def fully_read(self, path: Path, content: str) -> bool:
        """True when read_text has returned every character of `content` (the file as it is now)."""
        digest, spans = self.read_spans.get(str(path), ("", []))
        if digest != sha256_bytes(content.encode("utf-8")):
            return False
        reached = 0
        for s, e in sorted(spans):
            if s > reached:
                return False
            reached = max(reached, e)
        return reached >= len(content)

    def register(self, artifact: Artifact) -> Artifact:
        self.artifacts[str(artifact.path)] = artifact
        return artifact

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.artifacts_dir.parent)).replace("\\", "/")
        except ValueError:
            return str(p)
