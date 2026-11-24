from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    level_name = os.environ.get("BODHAN_MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = os.environ.get("BODHAN_MCP_LOG_FILE", "").strip()

    handler: logging.Handler
    if log_file:
        path = Path(log_file).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing, logging.StreamHandler) and getattr(existing, "stream", None) is sys.stdout:
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(max(level, logging.WARNING))
    logging.getLogger("httpcore").setLevel(max(level, logging.WARNING))
