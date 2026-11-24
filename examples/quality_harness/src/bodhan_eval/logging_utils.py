"""Structured JSONL logging. Every check and item event is a line so any flag can be
traced to the evidence behind it. Console output is human-readable; the JSONL file is
the machine trace. Timestamps live only in logs (never in the deterministic report files).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, TextIO

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}


class StructuredLogger:
    def __init__(self, jsonl_path: Path | None = None, level: str = "info"):
        self.level = _LEVELS.get(level, 20)
        self._fh: TextIO | None = None
        if jsonl_path is not None:
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = jsonl_path.open("w", encoding="utf-8")

    def _emit(self, level: str, event: str, **fields: Any) -> None:
        if _LEVELS[level] < self.level:
            return
        record = {"ts": round(time.time(), 3), "level": level, "event": event, **fields}
        if self._fh is not None:
            self._fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            self._fh.flush()
        if _LEVELS[level] >= self.level:
            item = f" [{fields.get('item')}]" if fields.get("item") else ""
            extra = " ".join(
                f"{k}={v}" for k, v in fields.items()
                if k != "item" and not isinstance(v, (dict, list))
            )
            stream = sys.stderr if _LEVELS[level] >= 30 else sys.stdout
            print(f"{level.upper():7} {event}{item} {extra}".rstrip(), file=stream)

    def debug(self, event: str, **f: Any) -> None:
        self._emit("debug", event, **f)

    def info(self, event: str, **f: Any) -> None:
        self._emit("info", event, **f)

    def warning(self, event: str, **f: Any) -> None:
        self._emit("warning", event, **f)

    def error(self, event: str, **f: Any) -> None:
        self._emit("error", event, **f)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
