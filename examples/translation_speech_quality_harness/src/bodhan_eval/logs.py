"""Structured logging: JSON lines to <out>/logs/run.jsonl, short human lines to stderr.

Log with `log.info("event_name", extra={"fields": {...}})`, or use `event()`.
Every per-check record carries item_id and check name so a flag can be traced to evidence.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = "bodhan_eval"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rec = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        rec.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            rec["exc"] = self.formatException(record.exc_info)
        return json.dumps(rec, ensure_ascii=False, default=str)


class _HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {}) or {}
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        return f"{record.levelname[0]} {record.getMessage()} {extra}".rstrip()


def setup_logging(out_dir: Path, level: str = "INFO") -> None:
    root = logging.getLogger(ROOT)
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.propagate = False
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(out_dir / "logs" / "run.jsonl", mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_JsonFormatter())
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level.upper())
    sh.setFormatter(_HumanFormatter())
    root.addHandler(fh)
    root.addHandler(sh)


def event(logger: logging.Logger, name: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, name, extra={"fields": fields})
