"""Append-only JSONL trace: one line per step, flushed immediately (survives crashes)."""

from __future__ import annotations

import json
import time
from pathlib import Path


class Trace:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.t0 = time.monotonic()
        self.n = 0
        self.events: list[dict] = []

    def log(self, kind: str, **fields) -> None:
        self.n += 1
        ev = {"step": self.n, "type": kind, "t_ms": int((time.monotonic() - self.t0) * 1000), **fields}
        self.events.append(ev)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
