"""Append-only JSONL trace: one line per event, flushed immediately so a crash keeps the history."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_FIELD = 4000


def _clip(value: Any, limit: int = MAX_FIELD) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"... [{len(value) - limit} chars clipped]"
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, limit) for v in value[:200]]
    return value


class Trace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.t0 = time.monotonic()
        self.events = 0

    def log(self, event: str, **fields: Any) -> None:
        self.events += 1
        line = {
            "seq": self.events,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "elapsed_s": round(time.monotonic() - self.t0, 3),
            "event": event,
            **_clip(fields),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
