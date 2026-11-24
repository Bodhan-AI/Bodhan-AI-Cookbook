"""Append-only step trace (trace.jsonl), written as it happens so a crashed or stopped run still has its history."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .guard import redact


class Trace:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.t0 = time.time()
        self.events: list[dict] = []

    def log(self, phase: str, kind: str, **fields: Any) -> dict:
        event = {"seq": len(self.events) + 1, "t_s": round(time.time() - self.t0, 3), "phase": phase, "kind": kind,
                 **redact(fields, 600)}
        self.events.append(event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event
