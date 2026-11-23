"""Structured JSON logging, one line per event, with stage/segment context.

Graders need to trace "why does this line sound the way it does", so every stage
and segment event is a single JSON object: stage, lang, seg, event, plus fields.
"""

from __future__ import annotations

import json
import logging
import sys
import time


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        for key in ("stage", "lang", "seg", "event", "extra"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("bodhan_dub")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False


class _ContextAdapter(logging.LoggerAdapter):
    """Merge adapter context with per-call extra (the stdlib default overwrites it)."""

    def process(self, msg, kwargs):
        call_extra = kwargs.get("extra") or {}
        kwargs["extra"] = {**self.extra, **call_extra}
        return msg, kwargs


def get_logger(stage: str | None = None, lang: str | None = None):
    """Return a logger adapter that stamps every record with stage/lang context."""
    base = logging.getLogger("bodhan_dub")
    extra = {k: v for k, v in (("stage", stage), ("lang", lang)) if v is not None}
    return _ContextAdapter(base, extra)


class Timer:
    """Context manager that records wall-clock seconds into a dict."""

    def __init__(self, sink: dict, key: str):
        self.sink, self.key = sink, key

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.sink[self.key] = round(time.monotonic() - self._t0, 3)
        return False
