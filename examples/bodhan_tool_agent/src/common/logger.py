"""Safe, structured logging for Bodhan MCP Server and Task Agent.

CRITICAL MCP STDIO PROTOCOL CONSTRAINT:
In MCP stdio servers (`python -m bodhan_mcp`), `stdout` is the JSON-RPC wire protocol channel.
Any accidental write to `stdout` corrupts the JSON-RPC framing and crashes the MCP client.
ALL logging strictly writes to `sys.stderr` (or file).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Union

# Formatter matching specification: %(asctime)s [%(levelname)s] %(name)s: %(message)s
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_FORMATTER = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)


class StderrStreamHandler(logging.StreamHandler):
    """StreamHandler that dynamically references sys.stderr on every emit.
    
    Ensures safe compatibility with stream redirections, test capture (capsys),
    and guarantees output NEVER leaks to sys.stdout.
    """
    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, val):
        pass

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


class AutoFlushFileHandler(logging.FileHandler):
    """FileHandler that automatically flushes after each emit for real-time trace visibility."""
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


_STREAM_HANDLER: StderrStreamHandler | None = None
_FILE_HANDLER: AutoFlushFileHandler | None = None
_INITIALIZED = False

# Root package logger name
BASE_LOGGER_NAME = "bodhan"
bodhan_logger = logging.getLogger(BASE_LOGGER_NAME)


def _resolve_level(level: Union[str, int, None]) -> int:
    """Resolve log level from string or int, defaulting to BODHAN_LOG_LEVEL or INFO."""
    if level is None:
        level_str = os.getenv("BODHAN_LOG_LEVEL", "INFO").strip().upper()
        return getattr(logging, level_str, logging.INFO)
    if isinstance(level, str):
        return getattr(logging, level.strip().upper(), logging.INFO)
    return level


def init_logging(
    level: Union[str, int, None] = None,
    log_file: Union[str, Path, None] = None,
) -> logging.Logger:
    """Initialize the Bodhan logger hierarchy.
    
    Attaches a StderrStreamHandler strictly writing to sys.stderr to protect MCP stdio.
    Optionally attaches an AutoFlushFileHandler if log_file is specified or BODHAN_LOG_FILE is set.
    """
    global _INITIALIZED, _STREAM_HANDLER, _FILE_HANDLER

    target_level = _resolve_level(level)
    bodhan_logger.setLevel(target_level)
    # Never propagate to root logger (which could inadvertently output to stdout)
    bodhan_logger.propagate = False

    # Configure dedicated stderr stream handler
    if _STREAM_HANDLER is None:
        for handler in bodhan_logger.handlers:
            if isinstance(handler, StderrStreamHandler):
                _STREAM_HANDLER = handler
                break
        if _STREAM_HANDLER is None:
            _STREAM_HANDLER = StderrStreamHandler()
            _STREAM_HANDLER.setFormatter(_FORMATTER)
            bodhan_logger.addHandler(_STREAM_HANDLER)

    _STREAM_HANDLER.setLevel(target_level)

    # Optional file handler
    file_path = log_file or os.getenv("BODHAN_LOG_FILE")
    if file_path:
        add_file_handler(file_path, level=target_level)

    _INITIALIZED = True
    return bodhan_logger


def add_file_handler(file_path: Union[str, Path], level: Union[str, int, None] = None) -> AutoFlushFileHandler:
    """Attach a file logger to the Bodhan logger hierarchy."""
    global _FILE_HANDLER
    p = Path(file_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    
    target_level = _resolve_level(level) if level is not None else bodhan_logger.level
    
    if _FILE_HANDLER is not None:
        if Path(_FILE_HANDLER.baseFilename).resolve() == p:
            _FILE_HANDLER.setLevel(target_level)
            return _FILE_HANDLER
        bodhan_logger.removeHandler(_FILE_HANDLER)
        _FILE_HANDLER.close()

    _FILE_HANDLER = AutoFlushFileHandler(str(p), encoding="utf-8")
    _FILE_HANDLER.setFormatter(_FORMATTER)
    _FILE_HANDLER.setLevel(target_level)
    bodhan_logger.addHandler(_FILE_HANDLER)
    return _FILE_HANDLER


def set_log_level(level: Union[str, int]) -> None:
    """Dynamically set the log level for bodhan_logger and all attached handlers."""
    target_level = _resolve_level(level)
    bodhan_logger.setLevel(target_level)
    for h in bodhan_logger.handlers:
        h.setLevel(target_level)


def flush_logs() -> None:
    """Flush all active log handlers."""
    for h in bodhan_logger.handlers:
        try:
            h.flush()
        except Exception:
            pass


def get_logger(name: str = BASE_LOGGER_NAME) -> logging.Logger:
    """Get a scoped logger under the bodhan namespace.
    
    All loggers inherit from `bodhan` and output strictly to `sys.stderr`
    to guarantee MCP JSON-RPC protocol safety on `stdout`.
    
    Args:
        name: Name or component identifier (e.g. 'mcp', 'agent', 'bodhan.mcp').
    """
    if not _INITIALIZED:
        init_logging()

    if not name or name == BASE_LOGGER_NAME:
        return bodhan_logger

    if name.startswith(f"{BASE_LOGGER_NAME}."):
        child_name = name
    elif name.startswith("bodhan_"):
        suffix = name[len("bodhan_"):]
        child_name = f"{BASE_LOGGER_NAME}.{suffix}"
    else:
        child_name = f"{BASE_LOGGER_NAME}.{name}"

    child_logger = logging.getLogger(child_name)
    child_logger.propagate = True
    return child_logger


# Default initialization on import
init_logging()
