"""Shared helpers for tools: the per-process context, output paths and previews."""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..client import BodhanClient
from ..config import Settings
from ..errors import BodhanToolError
from ..usage import Ledger


@dataclass
class ToolContext:
    settings: Settings
    client: BodhanClient


_CTX: ToolContext | None = None


def get_context() -> ToolContext:
    global _CTX
    if _CTX is None:
        settings = Settings.from_env()
        _CTX = ToolContext(settings, BodhanClient(settings))
    return _CTX


def set_context(ctx: ToolContext | None) -> None:
    """Tests inject a context with a fake transport."""
    global _CTX
    _CTX = ctx


_CURRENT_LEDGER: contextvars.ContextVar[Ledger | None] = contextvars.ContextVar("ledger", default=None)


def new_ledger(max_cost_inr: float | None) -> Ledger:
    if max_cost_inr is not None and max_cost_inr < 0:
        raise BodhanToolError("invalid_input", "max_cost_inr must be >= 0.")
    ledger = Ledger(max_cost_inr)
    _CURRENT_LEDGER.set(ledger)
    return ledger


def report_spend_on_error(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """If a paid tool fails after spending money, put the amount in the error (details.spent_inr)
    so callers can keep an exact budget even for failed calls."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _CURRENT_LEDGER.set(None)
        try:
            return await fn(*args, **kwargs)
        except BodhanToolError as exc:
            ledger = _CURRENT_LEDGER.get()
            if ledger is not None and ledger.spent > 0:
                raise exc.with_details(spent_inr=round(ledger.spent, 6)) from None
            raise
        finally:
            _CURRENT_LEDGER.reset(token)

    return wrapper


def output_dir(requested: str | None) -> Path:
    base = Path(requested).expanduser() if requested else get_context().settings.output_dir
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BodhanToolError("invalid_input", f"Cannot create output_dir {base}: {exc}") from exc
    return base.resolve()


def output_path(directory: Path, stem: str, tag: str, ext: str, fingerprint: Any) -> Path:
    """Deterministic, collision-free name: <stem>.<tag>.<hash>.<ext>."""
    digest = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:8]
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)[:60] or "output"
    return directory / f"{safe}.{tag}.{digest}.{ext}"


def preview(text: str, n: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
