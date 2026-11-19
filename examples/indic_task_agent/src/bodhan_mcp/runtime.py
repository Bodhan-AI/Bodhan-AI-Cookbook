"""Per-run state shared by every tool: cost ledger, cache, artifact registry, trace hook.

The MCP server creates one RunContext per server process; the agent creates one
per task run. Tools never touch globals, which keeps tests deterministic.
"""

from __future__ import annotations

import hashlib
import json
import math
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


def _scope_add(amount: float) -> None:
    scope = COST_SCOPE.get()
    if scope is not None:
        scope.append(amount)


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

    def add_llm(self, prompt_tokens: int, completion_tokens: int, cost: float | None) -> None:
        self.llm_calls += 1
        self.llm_prompt_tokens += prompt_tokens
        self.llm_completion_tokens += completion_tokens
        if cost is not None:
            self.llm_cost += cost
            self.llm_priced = True

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


class ResultCache:
    """Disk cache keyed by (model, exact input bytes, settings). Only successful,
    complete results are stored. `seen_this_run` lets us prove that no input was
    sent twice to the same model within one run."""

    def __init__(self, directory: Path | None):
        self.dir = directory
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
        self.memory: dict[str, tuple[dict, bytes | None]] = {}
        self.seen_this_run: set[str] = set()

    def _paths(self, key: str) -> tuple[Path, Path]:
        assert self.dir
        return self.dir / f"{key}.json", self.dir / f"{key}.bin"

    def get(self, key: str) -> tuple[dict, bytes | None, str] | None:
        if key in self.memory:
            meta, blob = self.memory[key]
            return meta, blob, "this_run"
        if self.dir:
            meta_p, bin_p = self._paths(key)
            if meta_p.is_file():
                try:
                    meta = json.loads(meta_p.read_text(encoding="utf-8"))
                    blob = bin_p.read_bytes() if bin_p.is_file() else None
                except (OSError, json.JSONDecodeError):
                    return None
                self.memory[key] = (meta, blob)
                return meta, blob, "previous_run"
        return None

    def put(self, key: str, meta: dict, blob: bytes | None = None) -> None:
        self.memory[key] = (meta, blob)
        self.seen_this_run.add(key)
        if self.dir:
            meta_p, bin_p = self._paths(key)
            try:
                if blob is not None:
                    bin_p.write_bytes(blob)
                meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass


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

    def as_dict(self, base: Path | None = None) -> dict[str, Any]:
        p = str(self.path.relative_to(base)) if base and self.path.is_relative_to(base) else str(self.path)
        return {"path": p.replace("\\", "/"), "kind": self.kind, "producer": self.producer, "language": self.language,
                "provenance": self.provenance, "verification": self.verification, "notes": self.notes}


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

    def register(self, artifact: Artifact) -> Artifact:
        self.artifacts[str(artifact.path)] = artifact
        return artifact

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.artifacts_dir.parent)).replace("\\", "/")
        except ValueError:
            return str(p)
