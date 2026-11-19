"""Append-only JSONL trace: one line per step, flushed immediately so a crash still leaves a record."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MAX_STR = 600


def compact(value: Any, limit: int = MAX_STR, depth: int = 0) -> Any:
    """Shorten long strings and lists so the trace stays readable but replayable."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"…(+{len(value) - limit} chars)"
    if isinstance(value, list):
        items = [compact(v, limit, depth + 1) for v in value[:20]]
        return items + ([f"…(+{len(value) - 20} items)"] if len(value) > 20 else [])
    if isinstance(value, dict):
        if depth > 6:
            return "{…}"
        return {k: compact(v, limit, depth + 1) for k, v in value.items()}
    return value


class Trace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")
        self.t0 = time.monotonic()
        self.lines = 0

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        record = {"seq": self.lines, "t_s": round(time.monotonic() - self.t0, 3), "kind": kind, **compact(fields)}
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        self.lines += 1
        return record

    def close(self) -> None:
        self._fh.close()
