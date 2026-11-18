"""Process-wide state for the server: one Bodhan client (shared cache, concurrency limit, spend ledger)."""

from __future__ import annotations

import traceback
from collections.abc import Awaitable

from bodhan_core.client import BodhanClient
from bodhan_core.config import load_settings
from bodhan_core.errors import ErrorCode, ToolError
from bodhan_core.ledger import Ledger

_client: BodhanClient | None = None


def get_client() -> BodhanClient:
    global _client
    if _client is None:
        settings = load_settings()
        _client = BodhanClient(settings, ledger=Ledger(limit_inr=settings.max_cost_inr))
    return _client


def set_client(client: BodhanClient | None) -> None:
    """Swap the client (tests inject one wired to mocked endpoints)."""
    global _client
    _client = client


async def safe(coro: Awaitable[dict]) -> dict:
    """Run a tool body; convert every failure into a structured error result. Never raises."""
    try:
        return await coro
    except ToolError as e:
        return e.to_result()
    except Exception as e:  # noqa: BLE001 - the server must never crash the client
        frame = traceback.extract_tb(e.__traceback__)[-1] if e.__traceback__ else None
        where = f" at {frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}" if frame else ""
        return ToolError(ErrorCode.INTERNAL, f"Unexpected {type(e).__name__}: {e}{where}",
                         hint="This is a bug in the tool; report it with the arguments used.").to_result()
