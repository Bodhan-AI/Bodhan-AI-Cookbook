from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class Trace:
    """One JSON line per event: plan, llm decision, tool call, verification, budget stop, finish."""

    def __init__(self, path: Path):
        self.path = path
        self.t0 = time.monotonic()
        self.n = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = path.open("w", encoding="utf-8")

    def log(self, type_: str, **fields: Any) -> None:
        self.n += 1
        rec = {"n": self.n, "t": round(time.monotonic() - self.t0, 2), "type": type_, **fields}
        self.f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()
