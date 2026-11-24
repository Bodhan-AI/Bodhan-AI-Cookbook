"""Structured logging.

Two sinks, on purpose:

* **Console** — short, human-readable, one line per event, for watching a run.
* **JSONL file** — one object per event, machine-readable, so any sentence in an
  output can be traced back to the call that produced it.

Both carry the same fields, including ``stage`` and ``segment`` where they apply.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER_NAME = "bodhan_dub"

_RESERVED = {"stage", "segment", "language", "event"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        fields = getattr(record, "fields", None) or {}
        stage = fields.get("stage", "-")
        head = f"{stamp} {record.levelname[:4]:<4} {str(stage):<9}"
        if fields.get("segment") is not None:
            head += f" seg={fields['segment']:<4}"
        body = record.getMessage()
        extras = " ".join(
            f"{key}={_short(value)}"
            for key, value in fields.items()
            if key not in _RESERVED and value is not None
        )
        line = f"{head} {body}"
        if extras:
            line += f"  {extras}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _short(value: Any) -> str:
    text = str(value)
    if len(text) > 60:
        return text[:57] + "..."
    return text


def setup_logging(level: str = "INFO", logfile: Path | None = None) -> logging.Logger:
    """Configure the package logger. Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(ConsoleFormatter())
    logger.addHandler(console)

    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(logfile, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def event(
    logger: logging.Logger,
    name: str,
    *,
    level: int = logging.INFO,
    stage: str | None = None,
    **fields: Any,
) -> None:
    """Emit one structured event. A missing logger is never an error."""
    if logger is None:
        return
    payload: dict[str, Any] = dict(fields)
    if stage is not None:
        payload["stage"] = stage
    logger.log(level, name, extra={"fields": payload})
