"""Simple progress lines for long pipeline runs (Docker-friendly, flushed)."""

from __future__ import annotations

import sys
from typing import Any


def log(message: str) -> None:
    """Print one progress line and flush (so Docker logs update live)."""
    print(message, flush=True, file=sys.stderr)


def stage(name: str, detail: str = "") -> None:
    """Banner when a pipeline stage starts."""
    extra = f" — {detail}" if detail else ""
    log(f"==> {name}{extra}")


def item(kind: str, index: int, total: int, name: str, status: str = "") -> None:
    """Per-page / per-lang / per-section progress."""
    tail = f" [{status}]" if status else ""
    log(f"  {kind} {index}/{total}: {name}{tail}")


def done(kind: str, **counts: Any) -> None:
    """Short stage completion line."""
    parts = ", ".join(f"{k}={v}" for k, v in counts.items())
    log(f"  {kind} done ({parts})")
