"""trace.jsonl: one JSON line per event, written as the run happens."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class RunLog:
    def __init__(self, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / "trace.jsonl"
        self.path.write_text("", encoding="utf-8")
        self._lock = threading.Lock()
        self._t0 = time.monotonic()

    def event(self, kind: str, **data: Any) -> None:
        line = {"t_s": round(time.monotonic() - self._t0, 3), "kind": kind, **data}
        encoded = json.dumps(line, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(encoded + "\n")


def shorten(value: Any, *, max_str: int = 1500, max_items: int = 8) -> Any:
    """Keep tool results small enough for the LLM context and the trace. Full outputs stay in the files."""
    if isinstance(value, str):
        if len(value) <= max_str:
            return value
        return value[:max_str] + f" ... [{len(value) - max_str} more characters in the output file]"
    if isinstance(value, list):
        items = [shorten(v, max_str=max_str, max_items=max_items) for v in value[:max_items]]
        if len(value) > max_items:
            items.append(f"... {len(value) - max_items} more items")
        return items
    if isinstance(value, dict):
        return {k: shorten(v, max_str=max_str, max_items=max_items) for k, v in value.items()}
    return value
