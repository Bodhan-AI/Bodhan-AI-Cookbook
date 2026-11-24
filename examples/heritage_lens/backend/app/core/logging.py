"""Logging setup with secret redaction (stdlib only)."""
from __future__ import annotations

import logging
import re
import threading

_secrets: set[str] = set()
_lock = threading.Lock()

_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]+"), r"\1***"),
    (re.compile(r"(?i)((?:api[_-]?key|secret[_-]?key|password|token)\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"), r"\1***"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{14,}|secret-key-[A-Za-z0-9_\-]+)\b"), r"***"),
]


def register_secret(value: str | None) -> None:
    """Register an exact secret string that must never appear in logs."""
    if value and len(value) >= 6:
        with _lock:
            _secrets.add(value)


def clear_secrets() -> None:
    with _lock:
        _secrets.clear()


def redact(text: str) -> str:
    if not text:
        return text
    with _lock:
        known = sorted(_secrets, key=len, reverse=True)
    for s in known:
        text = text.replace(s, "***")
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        record.msg = redact(message)
        record.args = ()
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(getattr(h, "_heritagelens", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler._heritagelens = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    # httpx logs full URLs at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
