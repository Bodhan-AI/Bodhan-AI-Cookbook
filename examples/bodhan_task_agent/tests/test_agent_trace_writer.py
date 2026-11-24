from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.core import guards
from bodhan_agent.core.trace import CONTENT_FIELDS, PREVIEW_CHARS, TraceWriter
from bodhan_agent.schemas.trace import LlmTokens, TraceError, TraceEvent

FIXED = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
TAMIL = "வணக்கம் உலகம், இது ஒரு சோதனை."


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def writer(tmp_path: Path, **kwargs: Any) -> TraceWriter:
    return TraceWriter(tmp_path / "out" / "trace.jsonl", clock=lambda: FIXED, **kwargs)


async def test_creates_folder_and_empty_file(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    assert trace.path.read_text() == ""


async def test_fresh_run_truncates_existing_trace(tmp_path: Path) -> None:
    path = tmp_path / "out" / "trace.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"seq":0,"old":true}\n')
    trace = writer(tmp_path)
    await trace.emit("preflight", "preflight", message="new run")
    assert [line["message"] for line in lines(path)] == ["new run"]


async def test_events_are_valid_json_lines(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await trace.emit("preflight", "preflight", message="2 inputs")
    await trace.emit("plan", "llm", llm_tokens=LlmTokens(prompt=100, completion=20))
    parsed = [TraceEvent.model_validate(line) for line in lines(trace.path)]
    assert [event.seq for event in parsed] == [0, 1]
    assert parsed == trace.events
    assert parsed[0].ts == FIXED


async def test_concurrent_emits_get_unique_consecutive_seq(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await asyncio.gather(*(trace.emit("act", "tool", tool="ocr_document", tool_call_id=f"c{i}") for i in range(25)))
    seqs = [line["seq"] for line in lines(trace.path)]
    assert seqs == list(range(25))
    assert sorted(line["tool_call_id"] for line in lines(trace.path)) == sorted(f"c{i}" for i in range(25))


async def test_cost_summed_without_source(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    for cost in (0.2, 0.0, 0.35):
        await trace.emit("act", "tool", tool="ocr_document", cost_inr=cost)
    assert [line["cum_cost_inr"] for line in lines(trace.path)] == pytest.approx([0.2, 0.2, 0.55])
    assert trace.cum_cost_inr == pytest.approx(0.55)


async def test_cost_source_is_authoritative_no_double_count(tmp_path: Path) -> None:
    ledger = {"spent": 0.0}
    trace = writer(tmp_path, cost_source=lambda: ledger["spent"])
    ledger["spent"] = 0.4
    await trace.emit("act", "tool", tool="ocr_document", cost_inr=0.2)
    await trace.emit("act", "tool", tool="ocr_document", cost_inr=0.2)
    records = lines(trace.path)
    assert [line["cost_inr"] for line in records] == [0.2, 0.2]
    assert [line["cum_cost_inr"] for line in records] == [0.4, 0.4]


async def test_cumulative_cost_never_decreases(tmp_path: Path) -> None:
    ledger = {"spent": 0.5}
    trace = writer(tmp_path, cost_source=lambda: ledger["spent"])
    await trace.emit("act", "budget")
    ledger["spent"] = 0.3
    await trace.emit("act", "budget")
    assert [line["cum_cost_inr"] for line in lines(trace.path)] == [0.5, 0.5]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1])
async def test_invalid_cost_source_rejected(tmp_path: Path, bad: float) -> None:
    trace = writer(tmp_path, cost_source=lambda: bad)
    with pytest.raises(ValueError, match="invalid total"):
        await trace.emit("act", "budget")
    assert trace.path.read_text() == ""


async def test_failed_event_does_not_consume_seq(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    with pytest.raises(ValueError):
        await trace.emit("act", "tool")
    await trace.emit("act", "budget")
    assert [line["seq"] for line in lines(trace.path)] == [0]


@pytest.mark.parametrize("field", sorted(CONTENT_FIELDS))
async def test_content_fields_become_summaries(tmp_path: Path, field: str) -> None:
    body = "Plants make food from sunlight. " * 10
    trace = writer(tmp_path)
    await trace.emit("act", "tool", tool="write", args={"path": "/out/work/a.txt", field: body})
    summary = lines(trace.path)[0]["args"][field]
    assert summary["redacted"] is True
    assert summary["chars"] == len(body)
    assert summary["sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert summary["preview"] == body[:PREVIEW_CHARS] + "…"


async def test_short_content_preview_has_no_ellipsis(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await trace.emit("act", "tool", tool="translate_text", args={"text": "Good morning"})
    assert lines(trace.path)[0]["args"]["text"]["preview"] == "Good morning"


async def test_content_preview_hides_indic_text(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await trace.emit("act", "tool", tool="translate_text", result_summary={"translation": TAMIL})
    summary = lines(trace.path)[0]["result_summary"]["translation"]
    assert summary["chars"] == len(TAMIL)
    assert not guards.contains_indic(summary["preview"])


async def test_nested_content_fields_summarised(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await trace.emit("act", "tool", tool="transcribe_audio", result_summary={"segments": [{"text": "hello there"}]})
    assert lines(trace.path)[0]["result_summary"]["segments"][0]["text"]["redacted"] is True


async def test_ordinary_fields_stay_readable(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    args = {"target_language": "ta", "format": "mp3", "voice": "anitha", "output_path": "/out/work/s.mp3"}
    await trace.emit(
        "act", "tool", tool="synthesize_speech", reason="Read the summary aloud", plan_step="S4", args=args
    )
    record = lines(trace.path)[0]
    assert record["args"] == args
    assert (record["reason"], record["plan_step"]) == ("Read the summary aloud", "S4")


async def test_indic_redacted_outside_path_fields(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await trace.emit(
        "act",
        "guard",
        message=f"Blocked answer: {TAMIL}",
        args={"file_path": "/in/பாடம்.pdf", "note": TAMIL},
    )
    record = lines(trace.path)[0]
    assert record["args"]["file_path"] == "/in/பாடம்.pdf"
    assert not guards.contains_indic(record["args"]["note"])
    assert not guards.contains_indic(record["message"])


async def test_secrets_scrubbed_everywhere(tmp_path: Path) -> None:
    secret = "sk-supersecret-123"
    trace = writer(tmp_path, secrets=[secret, ""])
    await trace.emit(
        "act",
        "tool",
        tool="translate_text",
        message=f"key {secret}",
        args={"header": f"Authorization: Bearer {secret}"},
        error=TraceError(code="auth_failed", message=f"bad key {secret}", hint="Authorization: Bearer abcdefghijkl"),
    )
    raw = trace.path.read_text()
    assert secret not in raw and "abcdefghijkl" not in raw
    record = lines(trace.path)[0]
    assert record["error"] == {
        "code": "auth_failed",
        "message": "bad key [secret]",
        "hint": "Authorization: Bearer [secret]",
    }


async def test_error_without_hint(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await trace.emit("act", "tool", tool="ocr_document", error=TraceError(code="timeout", message="No answer."))
    assert lines(trace.path)[0]["error"] == {"code": "timeout", "message": "No answer."}


async def test_long_strings_capped(tmp_path: Path) -> None:
    trace = writer(tmp_path, max_string_chars=10)
    await trace.emit("act", "budget", message="x" * 25, args={"note": "y" * 12})
    record = lines(trace.path)[0]
    assert record["message"] == "x" * 10 + "… [15 more chars]"
    assert record["args"]["note"] == "y" * 10 + "… [2 more chars]"


async def test_long_lists_capped(tmp_path: Path) -> None:
    trace = writer(tmp_path, max_list_items=3)
    await trace.emit("act", "tool", tool="ocr_document", result_summary={"pages_read": list(range(1, 8))})
    assert lines(trace.path)[0]["result_summary"]["pages_read"] == [1, 2, 3, "… [4 more items]"]


async def test_non_json_values_become_strings(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await trace.emit("act", "tool", tool="read", args={"path": Path("/out/work/a.txt"), "ids": ("A1", "A2"), "n": 3})
    assert lines(trace.path)[0]["args"] == {"path": "/out/work/a.txt", "ids": ["A1", "A2"], "n": 3}


async def test_unknown_llm_usage_is_empty_object(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await trace.emit("plan", "llm", llm_tokens=LlmTokens(prompt=None, completion=None))
    assert lines(trace.path)[0]["llm_tokens"] == {}


async def test_artifacts_and_latency_recorded(tmp_path: Path) -> None:
    trace = writer(tmp_path)
    await trace.emit("act", "tool", tool="ocr_document", artifacts=("A1", "A2"), latency_ms=812.5)
    record = lines(trace.path)[0]
    assert (record["artifacts"], record["latency_ms"]) == (["A1", "A2"], 812.5)
