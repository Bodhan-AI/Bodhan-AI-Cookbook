from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from bodhan_agent.schemas.trace import LlmTokens, TraceError, TraceEvent

NOW = datetime(2026, 11, 20, 10, 30, tzinfo=timezone.utc)


def event(**overrides: Any) -> TraceEvent:
    fields: dict[str, Any] = {"seq": 0, "ts": NOW, "phase": "act", "kind": "budget"}
    fields.update(overrides)
    return TraceEvent(**fields)


def test_tool_event_round_trips_as_one_json_line() -> None:
    item = event(
        seq=4,
        kind="tool",
        tool="ocr_document",
        tool_call_id="call_1",
        reason="Read the scanned chapter",
        plan_step="S1",
        args={"file_path": "/run/inputs/chapter.pdf"},
        result_summary={"pages_read": [1, 2, 3]},
        artifacts=["A1"],
        latency_ms=812.5,
        cost_inr=0.6,
        cum_cost_inr=0.6,
    )
    line = item.to_line()
    assert "\n" not in line
    assert TraceEvent.model_validate_json(line) == item
    assert json.loads(line)["ts"] == "2026-11-20T10:30:00Z"


def test_to_line_omits_unset_fields() -> None:
    data = json.loads(event(message="Budget reserved").to_line())
    assert set(data) == {"seq", "ts", "phase", "kind", "message", "artifacts", "cost_inr", "cum_cost_inr"}


def test_non_ascii_text_survives_serialization() -> None:
    item = event(message="Skipped [1,204 chars, Tamil script]", args={"note": "naïve café"})
    assert TraceEvent.model_validate_json(item.to_line()).args == {"note": "naïve café"}


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        event(ts=datetime(2026, 11, 20, 10, 30))


def test_offset_timestamp_accepted() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    assert event(ts=datetime(2026, 11, 20, 16, 0, tzinfo=ist)).ts.utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize("kind", ["tool", "uncertain_retry"])
def test_tool_kinds_must_name_tool(kind: str) -> None:
    with pytest.raises(ValidationError, match="must name its tool"):
        event(kind=kind)


def test_guard_event_without_tool_accepted() -> None:
    item = event(phase="finalize", kind="guard", message="Final answer contained Indian-language text; rejected.")
    assert item.tool is None


def test_guard_event_with_tool_accepted() -> None:
    assert event(kind="guard", tool="write").tool == "write"


def test_llm_event_needs_token_object() -> None:
    with pytest.raises(ValidationError, match="llm_tokens"):
        event(phase="plan", kind="llm")


def test_unknown_token_usage_is_not_zero() -> None:
    tokens = LlmTokens()
    assert tokens.prompt is None and tokens.completion is None
    assert tokens.total is None
    item = event(phase="plan", kind="llm", llm_tokens=tokens)
    assert json.loads(item.to_line())["llm_tokens"] == {}


def test_partial_token_usage_has_no_total() -> None:
    assert LlmTokens(prompt=120).total is None


def test_known_token_usage_totals() -> None:
    tokens = LlmTokens(prompt=120, completion=30)
    assert tokens.total == 150
    assert LlmTokens(prompt=0, completion=0).total == 0


def test_negative_tokens_rejected() -> None:
    with pytest.raises(ValidationError):
        LlmTokens(prompt=-1, completion=0)


@pytest.mark.parametrize("field", ["latency_ms", "cost_inr", "cum_cost_inr"])
def test_negative_numbers_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        event(**{field: -1})


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_non_finite_cost_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        event(cost_inr=value, cum_cost_inr=value)


def test_cumulative_cost_cannot_be_below_event_cost() -> None:
    with pytest.raises(ValidationError, match="cum_cost_inr"):
        event(cost_inr=1.0, cum_cost_inr=0.5)


def test_cumulative_cost_tolerates_float_rounding() -> None:
    assert event(cost_inr=0.1 + 0.2, cum_cost_inr=0.3).cum_cost_inr == 0.3


def test_negative_seq_rejected() -> None:
    with pytest.raises(ValidationError):
        event(seq=-1)


@pytest.mark.parametrize(("field", "value"), [("phase", "execute"), ("kind", "note")])
def test_unknown_phase_or_kind_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        event(**{field: value})


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        event(reasoning_content="hidden")


def test_error_payload() -> None:
    item = event(
        kind="tool",
        tool="translate_text",
        error=TraceError(code="timeout", message="No answer in 60 s.", hint="Retry once.", retryable=True),
    )
    data = json.loads(item.to_line())
    assert data["error"] == {
        "code": "timeout",
        "message": "No answer in 60 s.",
        "hint": "Retry once.",
        "retryable": True,
    }


def test_error_needs_code() -> None:
    with pytest.raises(ValidationError):
        TraceError(code="", message="x")
