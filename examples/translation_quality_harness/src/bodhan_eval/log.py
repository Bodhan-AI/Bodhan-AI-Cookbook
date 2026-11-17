"""Structured JSON-lines logging: one event per line, with run id, stage and item id."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

_logger = logging.getLogger("bodhan_eval")
_context: dict[str, Any] = {}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": round(record.created, 3), "level": record.levelname.lower(), "event": record.getMessage(), **_context}
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, ensure_ascii=False, default=str)


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {})
        extras = " ".join(f"{k}={v}" for k, v in fields.items() if k in {"item", "lang", "stage", "check", "error", "model", "count", "path"})
        return f"[{time.strftime('%H:%M:%S', time.localtime(record.created))}] {record.levelname.lower():7} {record.getMessage()} {extras}".rstrip()


def setup(log_path: Path | None, *, run_id: str, verbose: bool = False) -> None:
    _context.clear()
    _context["run_id"] = run_id
    _logger.handlers.clear()
    _logger.setLevel(logging.DEBUG)
    _logger.propagate = False
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(_ConsoleFormatter())
    _logger.addHandler(console)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(_JsonFormatter())
        _logger.addHandler(handler)


def close() -> None:
    for handler in list(_logger.handlers):
        handler.close()
        _logger.removeHandler(handler)


def event(name: str, level: int = logging.DEBUG, **fields: Any) -> None:
    _logger.log(level, name, extra={"fields": fields})


def info(name: str, **fields: Any) -> None:
    event(name, logging.INFO, **fields)


def warn(name: str, **fields: Any) -> None:
    event(name, logging.WARNING, **fields)
