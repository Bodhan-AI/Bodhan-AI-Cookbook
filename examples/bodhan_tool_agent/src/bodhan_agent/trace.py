"""trace.jsonl writer: one flushed line per event so a crashed run still leaves a replayable trace."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


def shorten(value: Any, limit: int = 200) -> Any:
    """Recursively truncate long strings so traces stay readable and free of bulk text."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"...[+{len(value) - limit} chars]"
    if isinstance(value, dict):
        return {k: shorten(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [shorten(v, limit) for v in value[:20]] + (["..."] if len(value) > 20 else [])
    return value


class Trace:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._t0 = time.time()
        path.write_text("", encoding="utf-8")

    def write(self, event: str, **fields: Any) -> None:
        row = {"t": round(time.time() - self._t0, 2), "event": event, **shorten(fields)}
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
