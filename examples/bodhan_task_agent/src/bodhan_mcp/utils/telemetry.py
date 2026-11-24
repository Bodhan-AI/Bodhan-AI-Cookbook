from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict

from bodhan_mcp.schemas.telemetry import CallRecord, UsageSummary

logger = logging.getLogger("bodhan_mcp.telemetry")

_current_calls: ContextVar[list[CallRecord] | None] = ContextVar("bodhan_mcp_calls", default=None)


@contextmanager
def call_scope() -> Iterator[list[CallRecord]]:
    calls: list[CallRecord] = []
    token = _current_calls.set(calls)
    try:
        yield calls
    finally:
        _current_calls.reset(token)


def record_call(record: CallRecord) -> None:
    calls = _current_calls.get()
    if calls is not None:
        calls.append(record)
    try:
        logger.info("bodhan_call %s", json.dumps(asdict(record), ensure_ascii=False, allow_nan=False))
    except Exception:
        logger.warning("telemetry: failed to log a Bodhan call", exc_info=True)


def summarize(calls: list[CallRecord], latency_ms: float) -> UsageSummary:
    sent = [c for c in calls if not c.cached]
    known = [c.estimated_cost_inr for c in sent if c.estimated_cost_inr is not None]
    return UsageSummary(
        model_calls=len(sent),
        http_attempts=sum(c.attempts for c in sent),
        estimated_cost_inr=round(sum(known), 6),
        billing_uncertain=any(c.billing_uncertain for c in sent) or len(known) < len(sent),
        possibly_billed_failed_attempts=sum(c.possibly_billed_failed_attempts for c in sent),
        cache_hits=len(calls) - len(sent),
        latency_ms=round(max(latency_ms, 0.0), 1),
    )


def log_tool_summary(tool: str, status: str, usage: UsageSummary | None) -> None:
    payload = {"tool": tool, "status": status, "usage": usage.model_dump() if usage else None}
    try:
        logger.info("bodhan_tool %s", json.dumps(payload, ensure_ascii=False, allow_nan=False))
    except Exception:
        logger.warning("telemetry: failed to log tool summary", exc_info=True)
