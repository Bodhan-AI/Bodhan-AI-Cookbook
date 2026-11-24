"""Append-only JSONL trace: one line per step (plan, tool call, verify, retry, stop)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class Trace:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.step = 0

    def log(self, phase: str, **fields: Any) -> dict:
        self.step += 1
        entry = {"ts": round(time.time(), 3), "step": self.step, "phase": phase, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry
