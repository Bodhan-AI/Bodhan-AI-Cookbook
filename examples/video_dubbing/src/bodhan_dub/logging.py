"""
Structured JSONL logging for bodhan_dub.
Ensures every log entry includes run_id, stage, lang, segment_id, and attempt,
and automatically redacts sensitive tokens/keys.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

# Regex patterns to detect sensitive API keys and auth headers
SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9_-]{8,}"),
    re.compile(r"gsk_[a-zA-Z0-9_-]{8,}"),
    re.compile(r"AIza[a-zA-Z0-9_-]{16,}"),
]

_REGISTERED_SECRETS: Set[str] = set()


def register_secret(secret: Optional[str]) -> None:
    """Registers a secret key/token to be redacted from all log output."""
    if secret and isinstance(secret, str) and len(secret.strip()) >= 4:
        _REGISTERED_SECRETS.add(secret.strip())


def sanitize_text(text: str) -> str:
    """Replaces API keys and registered secrets in strings with safe redacted placeholders."""
    if not isinstance(text, str):
        return text
    sanitized = text
    # First redact explicitly registered secret values
    for sec in sorted(_REGISTERED_SECRETS, key=len, reverse=True):
        if sec in sanitized:
            sanitized = sanitized.replace(sec, "[REDACTED]")
    # Next redact pattern-matching shapes (Bearer, sk-, gsk_, AIza)
    for pat in SECRET_PATTERNS:
        if "Bearer" in pat.pattern:
            sanitized = pat.sub("Bearer [REDACTED]", sanitized)
        else:
            sanitized = pat.sub("[REDACTED]", sanitized)
    return sanitized


def sanitize_data(data: Any) -> Any:
    """Recursively redacts secrets in dicts, lists, and strings."""
    if isinstance(data, str):
        return sanitize_text(data)
    elif isinstance(data, dict):
        return {k: sanitize_data(v) for k, v in data.items()}
    elif isinstance(data, (list, tuple)):
        res = [sanitize_data(v) for v in data]
        return type(data)(res)
    return data


class SanitizingFormatter(logging.Formatter):
    """Console formatter that automatically sanitizes all formatted output."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return sanitize_text(formatted)


class JsonlHandler(logging.Handler):
    """Appends structured JSON log records to a .jsonl file."""

    def __init__(self, filepath: Path):
        super().__init__()
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.filepath, "a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = self.format_entry(record)
            self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._file.flush()
        except Exception:
            self.handleError(record)

    def format_entry(self, record: logging.LogRecord) -> Dict[str, Any]:
        msg = record.getMessage()
        sanitized_msg = sanitize_text(msg)

        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitized_msg,
            "run_id": getattr(record, "run_id", "default"),
            "stage": getattr(record, "stage", "system"),
            "lang": getattr(record, "lang", None),
            "segment_id": getattr(record, "segment_id", None),
            "attempt": getattr(record, "attempt", 1),
        }
        # Include any extra metadata passed
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            entry["extra"] = sanitize_data(record.extra_data)
        return entry

    def close(self) -> None:
        if hasattr(self, "_file") and not self._file.closed:
            self._file.close()
        super().close()


class PipelineLogger:
    """Wrapper that injects pipeline context (run_id, stage, lang, etc.) into logs."""

    def __init__(
        self,
        name: str = "bodhan_dub",
        run_id: str = "run-001",
        log_file: Optional[Path] = None,
        level: int = logging.INFO,
    ):
        self.run_id = run_id
        self.logger = logging.getLogger(f"{name}.{run_id}")
        self.logger.setLevel(level)
        self.logger.propagate = False

        # Clear existing handlers
        self.logger.handlers.clear()

        # Console handler with clean human-readable output
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        fmt = SanitizingFormatter(
            "[%(asctime)s] %(levelname)-7s [%(stage)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        ch.setFormatter(fmt)
        self.logger.addHandler(ch)

        # JSONL file handler if path provided
        if log_file:
            jh = JsonlHandler(log_file)
            jh.setLevel(level)
            self.logger.addHandler(jh)

    def log(
        self,
        level: int,
        msg: str,
        stage: str = "system",
        lang: Optional[str] = None,
        segment_id: Optional[str] = None,
        attempt: int = 1,
        **extra: Any,
    ) -> None:
        sanitized_msg = sanitize_text(msg)
        sanitized_extra = sanitize_data(extra) if extra else None
        self.logger.log(
            level,
            sanitized_msg,
            extra={
                "run_id": self.run_id,
                "stage": stage,
                "lang": lang,
                "segment_id": segment_id,
                "attempt": attempt,
                "extra_data": sanitized_extra,
            },
        )

    def info(self, msg: str, **kwargs: Any) -> None:
        self.log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self.log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self.log(logging.ERROR, msg, **kwargs)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self.log(logging.DEBUG, msg, **kwargs)

    def flush(self) -> None:
        """Flushes all log handlers."""
        for h in self.logger.handlers:
            h.flush()

    def save_log(self, filepath: Optional[Path] = None) -> None:
        """Flushes active handlers to disk and ensures log file exists."""
        self.flush()
        if filepath:
            filepath = Path(filepath)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            if not filepath.exists():
                filepath.touch()
