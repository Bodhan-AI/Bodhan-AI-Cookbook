from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bodhan_agent import guards
from bodhan_agent.schemas.trace import EventKind, LlmTokens, Phase, TraceError, TraceEvent

MAX_STRING_CHARS = 2_000
MAX_LIST_ITEMS = 50
PREVIEW_CHARS = 80
CONTENT_FIELDS: frozenset[str] = frozenset(
    {"content", "text", "transcript", "translation", "markdown", "ocr_text", "raw_text", "base64"}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TraceWriter:
    def __init__(
        self,
        path: Path,
        secrets: Iterable[str] = (),
        cost_source: Callable[[], float] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        max_string_chars: int = MAX_STRING_CHARS,
        max_list_items: int = MAX_LIST_ITEMS,
    ) -> None:
        self.path = path
        self.events: list[TraceEvent] = []
        self._secrets = tuple(secret for secret in secrets if secret)
        self._cost_source = cost_source
        self._clock = clock
        self._max_string_chars = max_string_chars
        self._max_list_items = max_list_items
        self._lock = asyncio.Lock()
        self._seq = 0
        self._cum_cost = 0.0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    @property
    def cum_cost_inr(self) -> float:
        return self._cum_cost

    def _clean_text(self, text: str, key: str | None, cap: bool = True) -> str:
        text = guards.scrub_secrets(text, self._secrets)
        if key not in guards.PATH_FIELDS:
            text, _ = guards.redact_text(text)
        if cap and len(text) > self._max_string_chars:
            hidden = len(text) - self._max_string_chars
            text = f"{text[: self._max_string_chars]}… [{hidden:,} more chars]"
        return text

    def _content_summary(self, text: str) -> dict[str, Any]:
        preview = self._clean_text(text[:PREVIEW_CHARS], None, cap=False)
        return {
            "redacted": True,
            "chars": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "preview": preview + ("…" if len(text) > PREVIEW_CHARS else ""),
        }

    def _clean(self, value: Any, key: str | None = None) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self._content_summary(value) if key in CONTENT_FIELDS else self._clean_text(value, key)
        if isinstance(value, Path):
            return self._clean_text(str(value), key)
        if isinstance(value, dict):
            return {str(inner_key): self._clean(inner, str(inner_key)) for inner_key, inner in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
            cleaned = [self._clean(item, key) for item in items[: self._max_list_items]]
            if len(items) > self._max_list_items:
                cleaned.append(f"… [{len(items) - self._max_list_items:,} more items]")
            return cleaned
        return self._clean_text(str(value), key)

    def _clean_error(self, error: TraceError | None) -> TraceError | None:
        if error is None:
            return None
        return error.model_copy(
            update={
                "message": self._clean_text(error.message, "message"),
                "hint": None if error.hint is None else self._clean_text(error.hint, "message"),
            }
        )

    def _next_cost(self, cost_inr: float) -> float:
        if self._cost_source is None:
            return self._cum_cost + cost_inr
        actual = self._cost_source()
        if not math.isfinite(actual) or actual < 0:
            raise ValueError(f"cost source returned an invalid total: {actual!r}")
        return max(self._cum_cost, actual)

    async def emit(
        self,
        phase: Phase,
        kind: EventKind,
        *,
        message: str | None = None,
        tool_call_id: str | None = None,
        tool: str | None = None,
        reason: str | None = None,
        plan_step: str | None = None,
        args: dict[str, Any] | None = None,
        result_summary: dict[str, Any] | None = None,
        error: TraceError | None = None,
        artifacts: Iterable[str] = (),
        latency_ms: float | None = None,
        cost_inr: float = 0.0,
        llm_tokens: LlmTokens | None = None,
    ) -> TraceEvent:
        async with self._lock:
            cum_cost = self._next_cost(cost_inr)
            event = TraceEvent(
                seq=self._seq,
                ts=self._clock(),
                phase=phase,
                kind=kind,
                message=None if message is None else self._clean_text(message, "message"),
                tool_call_id=tool_call_id,
                tool=tool,
                reason=None if reason is None else self._clean_text(reason, "reason"),
                plan_step=plan_step,
                args=self._clean(args),
                result_summary=self._clean(result_summary),
                error=self._clean_error(error),
                artifacts=list(artifacts),
                latency_ms=latency_ms,
                cost_inr=cost_inr,
                cum_cost_inr=cum_cost,
                llm_tokens=llm_tokens,
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(event.to_line() + "\n")
            self._seq += 1
            self._cum_cost = cum_cost
            self.events.append(event)
            return event
