"""Append-only JSONL trace: one line per agent event, flushed as it happens.

Lines are written immediately so a run that is killed mid-way still leaves a
replayable record of every decision up to that point.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

EVENT_TYPES = frozenset({"inventory", "plan", "llm", "tool", "verify", "guard", "finish", "error"})
_MAX_ARG_CHARS = 300
_MAX_SUMMARY_CHARS = 2000


def is_path_key(key: str) -> bool:
    k = key.lower()
    return k == "path" or k == "paths" or k.endswith("_path") or k.endswith("_paths") or k == "artifacts"


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [+{len(text) - limit} chars]"


def summarize_arguments(value: Any, key: str = "", limit: int = _MAX_ARG_CHARS) -> Any:
    """Copy arguments for the trace, keeping paths whole and shortening long free text."""
    if isinstance(value, dict):
        return {k: summarize_arguments(v, str(k), limit) for k, v in value.items()}
    if isinstance(value, list):
        return [summarize_arguments(v, key, limit) for v in value]
    if isinstance(value, str) and not is_path_key(key):
        return truncate(value, limit)
    return value


def _to_jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class TraceWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO = path.open("w", encoding="utf-8")
        self._step = 0
        self.cumulative_cost_inr = 0.0
        self.events: list[dict[str, Any]] = []

    def log(
        self,
        type: str,
        *,
        turn: int | None = None,
        tool: str | None = None,
        arguments: Any = None,
        result_summary: Any = None,
        latency_s: float | None = None,
        cost_inr: float = 0.0,
        cache_hits: int = 0,
        llm_tokens: dict[str, int] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown trace event type {type!r}")
        self._step += 1
        self.cumulative_cost_inr = round(self.cumulative_cost_inr + (cost_inr or 0.0), 6)
        summary = result_summary
        if summary is not None and not isinstance(summary, str):
            summary = _to_jsonable(summary)
            encoded = json.dumps(summary, ensure_ascii=False)
            if len(encoded) > _MAX_SUMMARY_CHARS:
                summary = truncate(encoded, _MAX_SUMMARY_CHARS)
        elif isinstance(summary, str):
            summary = truncate(summary, _MAX_SUMMARY_CHARS)
        event: dict[str, Any] = {
            "step": self._step,
            "turn": turn,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "type": type,
            "tool": tool,
            "arguments": summarize_arguments(arguments) if arguments is not None else None,
            "result_summary": summary,
            "latency_s": round(latency_s, 3) if latency_s is not None else None,
            "cost_inr": round(cost_inr or 0.0, 6),
            "cumulative_cost_inr": self.cumulative_cost_inr,
            "cache_hits": cache_hits,
            "llm_tokens": llm_tokens,
        }
        event.update({k: _to_jsonable(v) for k, v in extra.items()})
        self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.events.append(event)
        return event

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


class Stopwatch:
    def __init__(self) -> None:
        self._t0 = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self._t0
