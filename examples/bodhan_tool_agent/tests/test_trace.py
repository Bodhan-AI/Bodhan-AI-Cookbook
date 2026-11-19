"""Unit tests for the TraceLogger and Report Generator (src.bodhan_agent.trace)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.bodhan_agent.trace import TraceLogger, classify_error, slugify
from src.common.config import BodhanConfig
from src.common.ledger import CostLedger


def test_slugify():
    # Normal sentence
    assert slugify("Make a two-minute Tamil audio summary of this chapter") == "make-a-two-minute-tamil-audio-summa"
    # Special characters and punctuation
    assert slugify("Analyze & summarize PDF: Q3 report (v1.2)!") == "analyze-summarize-pdf-q3-report-v1"
    # Max length boundary and no trailing hyphens
    assert len(slugify("a" * 50, max_length=35)) <= 35
    assert not slugify("hello world - - -").endswith("-")
    # Empty / whitespace fallback
    assert slugify("   ") == "run"
    assert slugify("---") == "run"
    # Preserve underscores and alphanumeric
    assert slugify("task_run_123") == "task_run_123"


def test_trace_logging(tmp_path: Path):
    logger = TraceLogger(tmp_path)
    
    # Log two steps
    logger.log_step(
        step=1,
        plan="Inspect media file",
        tool="inspect_media",
        arguments={"file_path": "test.wav"},
        result_summary="WAV audio, 12.5s",
        latency_ms=120,
        cost_inr=0.0,
        cumulative_cost_inr=0.0,
    )
    logger.log_step(
        step=2,
        plan="Transcribe audio",
        tool="transcribe_audio",
        arguments={"file_path": "test.wav"},
        result_summary="Transcribed 40 words",
        latency_ms=850,
        cost_inr=0.05,
        cumulative_cost_inr=0.05,
    )

    trace_file = tmp_path / "trace.jsonl"
    assert trace_file.exists()

    lines = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").strip().split("\n")]
    assert len(lines) == 2
    assert lines[0]["step"] == 1
    assert lines[0]["tool"] == "inspect_media"
    assert lines[1]["step"] == 2
    assert lines[1]["cost_inr"] == 0.05


def test_write_report_and_result(tmp_path: Path):
    logger = TraceLogger(tmp_path)
    ledger = CostLedger(max_budget_inr=10.0)
    ledger.record_operation("indic-ocr", units=2, step=1)

    logger.write_report_and_result(
        task="Test task",
        status="completed",
        final_answer="Here is the final output text.",
        cost_ledger=ledger,
        verification_details={"verified": True, "notes": "All checks passed"},
        assumptions=["Assumed Hindi audio"],
    )

    report_file = tmp_path / "report.json"
    result_file = tmp_path / "result.md"

    assert report_file.exists()
    assert result_file.exists()

    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_data["status"] == "completed"
    assert report_data["total_cost_inr"] == 0.40
    assert report_data["verification"]["verified"] is True
    assert "run_id" in report_data
    assert "timestamp" in report_data

    result_text = result_file.read_text(encoding="utf-8")
    assert "Here is the final output text." in result_text
    assert "Assumed Hindi audio" in result_text


def test_post_run_mirroring(tmp_path: Path):
    logger = TraceLogger(tmp_path, task="Post Run Mirroring Task")
    (logger.artifacts_dir / "summary.txt").write_text("Test artifact content", encoding="utf-8")

    ledger = CostLedger(max_budget_inr=5.0)
    logger.log_step(
        step=1,
        plan="Create summary artifact",
        tool="write_text_file",
        arguments={"file_path": "summary.txt"},
        result_summary="Wrote summary.txt",
        latency_ms=10,
        cost_inr=0.0,
        cumulative_cost_inr=0.0,
    )
    logger.write_report_and_result(
        task="Post Run Mirroring Task",
        status="completed",
        final_answer="Done",
        cost_ledger=ledger,
    )

    assert logger.archive_dir is not None
    assert logger.archive_dir.is_dir()
    assert (logger.archive_dir / "report.json").exists()
    assert (logger.archive_dir / "result.md").exists()
    assert (logger.archive_dir / "trace.jsonl").exists()
    assert (logger.archive_dir / "artifacts" / "summary.txt").exists()
    assert (logger.archive_dir / "artifacts" / "summary.txt").read_text(encoding="utf-8") == "Test artifact content"

    rep = json.loads((logger.archive_dir / "report.json").read_text(encoding="utf-8"))
    assert rep["run_id"] == logger.run_id
    assert rep["artifacts"] == ["summary.txt"]


def test_sequential_runs_do_not_contaminate_artifacts(tmp_path: Path):
    ledger = CostLedger(max_budget_inr=10.0)

    # Run 1: writes artifact_run1.txt
    run1 = TraceLogger(tmp_path, task="Run One Task")
    (run1.artifacts_dir / "artifact_run1.txt").write_text("Run 1 Artifact", encoding="utf-8")
    run1.log_step(
        step=1,
        plan="Produce Run 1 artifact",
        tool="write_text_file",
        arguments={},
        result_summary="Wrote artifact 1",
        latency_ms=10,
        cost_inr=0.0,
        cumulative_cost_inr=0.0,
    )
    run1.write_report_and_result(
        task="Run One Task",
        status="completed",
        final_answer="Run 1 Completed",
        cost_ledger=ledger,
    )

    rep1 = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert rep1["artifacts"] == ["artifact_run1.txt"]
    run1_archive = run1.archive_dir
    assert run1_archive is not None
    assert (run1_archive / "artifacts" / "artifact_run1.txt").exists()

    # Run 2 against the exact same tmp_path: writes artifact_run2.txt
    run2 = TraceLogger(tmp_path, task="Run Two Task")

    # Invariant: run2 starts with a completely empty artifacts directory
    assert list(run2.artifacts_dir.iterdir()) == []

    (run2.artifacts_dir / "artifact_run2.txt").write_text("Run 2 Artifact", encoding="utf-8")
    run2.log_step(
        step=1,
        plan="Produce Run 2 artifact",
        tool="write_text_file",
        arguments={},
        result_summary="Wrote artifact 2",
        latency_ms=10,
        cost_inr=0.0,
        cumulative_cost_inr=0.0,
    )
    run2.write_report_and_result(
        task="Run Two Task",
        status="completed",
        final_answer="Run 2 Completed",
        cost_ledger=ledger,
    )

    rep2 = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    # CRITICAL INVARIANT: Run 2's artifacts must ONLY contain artifact_run2.txt, NO contamination!
    assert rep2["artifacts"] == ["artifact_run2.txt"]
    assert "artifact_run1.txt" not in rep2["artifacts"]

    # Verify Run 1's archive is preserved and isolated
    assert (run1_archive / "artifacts" / "artifact_run1.txt").exists()
    assert not (run1_archive / "artifacts" / "artifact_run2.txt").exists()

    # Verify Run 2's archive has only artifact_run2.txt
    run2_archive = run2.archive_dir
    assert run2_archive is not None
    assert (run2_archive / "artifacts" / "artifact_run2.txt").exists()
    assert not (run2_archive / "artifacts" / "artifact_run1.txt").exists()


def test_crash_recovery_archiving(tmp_path: Path):
    # Simulate a crashed run: trace.jsonl and an artifact exist, but report.json does NOT exist
    trace_file = tmp_path / "trace.jsonl"
    step_data = {
        "step": 1,
        "timestamp": "2026-11-18T10:00:00Z",
        "plan": "Crash Recovery Simulated Step",
        "tool": "transcribe_audio",
        "arguments": {},
        "result_summary": "Halfway done before crashing",
        "latency_ms": 200,
        "cost_inr": 0.05,
        "cumulative_cost_inr": 0.05,
        "status": "success",
    }
    trace_file.write_text(json.dumps(step_data) + "\n", encoding="utf-8")

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "partial_output.txt").write_text("Unfinished data", encoding="utf-8")

    # Ensure report.json does NOT exist
    report_file = tmp_path / "report.json"
    assert not report_file.exists()

    # Now start a new run
    logger = TraceLogger(tmp_path, task="Fresh Task After Crash")

    # 1. Active artifacts directory should be clean
    assert list(logger.artifacts_dir.iterdir()) == []

    # 2. Crashed run should be archived in runs/
    runs_dir = tmp_path / "runs"
    assert runs_dir.exists()
    archived_runs = list(runs_dir.iterdir())
    assert len(archived_runs) >= 1

    # Find the crashed archive
    crashed_archive = None
    for r in archived_runs:
        if (r / "artifacts" / "partial_output.txt").exists():
            crashed_archive = r
            break

    assert crashed_archive is not None, "Crashed run artifacts were not preserved in runs/"
    assert (crashed_archive / "trace.jsonl").exists()
    assert (crashed_archive / "artifacts" / "partial_output.txt").read_text(encoding="utf-8") == "Unfinished data"


def test_write_report_and_result_with_plan_and_impossible_status(tmp_path: Path):
    logger = TraceLogger(tmp_path, task="Translate video to French")
    ledger = CostLedger(max_budget_inr=10.0)

    plan = {
        "goal": "Translate video to French",
        "feasible": False,
        "infeasibility_reason": "French is not an Indic language supported by Bodhan",
        "assumptions": ["Audio format is WAV"],
        "steps": [],
        "target_language": "fr",
    }

    logger.log_step(
        step=0,
        plan="Plan: Translate video to French",
        tool="submit_plan",
        arguments=plan,
        result_summary="Infeasible task: French is not an Indic language",
        latency_ms=100,
        cost_inr=0.0,
        cumulative_cost_inr=0.0,
        status="halted",
    )

    logger.write_report_and_result(
        task="Translate video to French",
        status="impossible",
        final_answer="Task cannot be completed: French is unsupported.",
        cost_ledger=ledger,
        verification_details={"verified": False, "notes": "Refused during planning"},
        assumptions=["Audio format is WAV"],
        plan=plan,
    )

    report_data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report_data["status"] == "impossible"
    assert report_data["plan"]["goal"] == "Translate video to French"
    assert report_data["assumptions"] == ["Audio format is WAV"]
    assert report_data["total_cost_inr"] == 0.0

    result_text = (tmp_path / "result.md").read_text(encoding="utf-8")
    assert "**Status:** `IMPOSSIBLE`" in result_text
    assert "## Execution Plan" in result_text
    assert "**Goal:** Translate video to French" in result_text
    assert "Task cannot be completed: French is unsupported." in result_text


# ---------------------------------------------------------------------------
# Trace schema v2 tests (additive)
# ---------------------------------------------------------------------------

SPAN_ID_RE = re.compile(r"^s\d{4}$")


def _read_jsonl(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def _fake_tool_call(tc_id: str, name: str, arguments: dict) -> SimpleNamespace:
    """Mimic an OpenAI tool_calls entry: .function.name / .function.arguments."""
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _log_basic_step(logger: TraceLogger, step: int, **kwargs):
    base = dict(
        step=step,
        plan=f"Plan {step}",
        tool="translate_text",
        arguments={},
        result_summary="ok",
        latency_ms=5,
        cost_inr=0.0,
        cumulative_cost_inr=0.0,
    )
    base.update(kwargs)
    return logger.log_step(**base)


def test_step_record_v2_fields_present(tmp_path: Path):
    logger = TraceLogger(tmp_path)
    _log_basic_step(logger, step=1)

    rec = _read_jsonl(tmp_path / "trace.jsonl")[0]
    assert rec["schema_version"] == 2
    assert rec["run_id"] == logger.run_id
    assert rec["run_id"] != ""
    assert SPAN_ID_RE.match(rec["span_id"])
    assert rec["span_type"] == "tool"
    assert rec["error_class"] is None
    assert rec["parent_span_id"] is None
    assert rec["output_ref"] is None
    assert rec["output_sha256"] is None
    assert rec["output_chars"] is None


def test_span_ids_monotonic_across_llm_and_tool_spans(tmp_path: Path):
    logger = TraceLogger(tmp_path)
    llm_span = logger.log_llm_span(step=1, plan="p", model="m", latency_ms=1)
    rec1 = _log_basic_step(logger, step=1)
    rec2 = _log_basic_step(logger, step=2)
    llm_span2 = logger.log_llm_span(step=3, plan="p", model="m", latency_ms=1)
    rec3 = _log_basic_step(logger, step=3)

    ids = [llm_span, rec1.span_id, rec2.span_id, llm_span2, rec3.span_id]
    assert ids == ["s0001", "s0002", "s0003", "s0004", "s0005"]
    assert SPAN_ID_RE.match(ids[0])


def test_full_output_spilling(tmp_path: Path):
    logger = TraceLogger(tmp_path, task="Spill Task")
    content = "full tool output\nwith lines\n" * 10
    summary = "full tool output\nwith li..."

    rec = _log_basic_step(logger, step=2, result_summary=summary, full_output=content)

    span_files = list((tmp_path / "spans").iterdir())
    assert len(span_files) == 1
    span_file = span_files[0]
    assert span_file.name == f"{rec.span_id}_translate_text-output.txt"
    assert span_file.read_text(encoding="utf-8") == content
    assert rec.output_sha256 == hashlib.sha256(span_file.read_bytes()).hexdigest()
    assert rec.output_chars == len(content)
    assert rec.output_ref == f"spans/{span_file.name}"
    assert "\\" not in rec.output_ref

    # The trace record keeps the truncated preview unchanged
    trace_rec = _read_jsonl(tmp_path / "trace.jsonl")[0]
    assert trace_rec["result_summary"] == summary


def test_spill_cap_truncates_bytes_keeps_full_char_count(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_TRACE_MAX_OUTPUT_BYTES", "100")
    logger = TraceLogger(tmp_path)
    content = "x" * 500

    rec = _log_basic_step(logger, step=1, full_output=content)

    span_file = next((tmp_path / "spans").iterdir())
    size = span_file.stat().st_size
    assert 0 < size <= 110
    assert span_file.read_text(encoding="utf-8").startswith("x")
    assert rec.output_chars == 500
    assert rec.output_sha256 == hashlib.sha256(span_file.read_bytes()).hexdigest()


def test_spill_default_cap_writes_full_output(tmp_path: Path):
    logger = TraceLogger(tmp_path)
    content = "y" * 100_000

    rec = _log_basic_step(logger, step=1, full_output=content)

    span_file = next((tmp_path / "spans").iterdir())
    assert span_file.stat().st_size == len(content.encode("utf-8"))
    assert rec.output_chars == len(content)


def test_classify_error_taxonomy():
    cases = [
        # text, status, expected class
        ("HTTP 429 Too Many Requests", "error", "rate_limit"),
        ("Rate limit reached for translations", "error", "rate_limit"),
        # rate limit wins over network-ish words (explicit contract)
        ("429 exceeded: ConnectTimeout ... unreachable ... Bodhan service may be down", "error", "rate_limit"),
        ("unsupported_language: bhb is refused by Indic-Translate", "error", "unsupported_language"),
        ("BudgetExceededError: ₹5 exceeds the remaining ₹2", "error", "budget"),
        ("Estimated Bodhan cost ₹0.40 exceeds the remaining estimated budget ₹0.20.", "error", "budget"),
        ("Policy: Indic-script text must originate from a Bodhan model", "error", "policy_refusal"),
        ("provenance_guard refused the write", "error", "policy_refusal"),
        ("Request refused by policy: Indic text must come from Bodhan tools", "error", "policy_refusal"),
        ("EstimateError: cannot estimate", "error", "preflight"),
        ("Pre-flight estimate unavailable; paid tool was not called.", "error", "preflight"),
        ("ConnectTimeout: service unreachable", "error", "network"),
        ("ConnectError", "error", "network"),
        ("The Bodhan service may be down", "error", "network"),
        ("Tool call exceeded AGENT_TOOL_TIMEOUT_S=300s", "error", "timeout"),
        ("Tool timed out after 300s", "error", "timeout"),
        ("Mystery failure xyz", "error", "other"),
    ]
    for text, status, expected in cases:
        assert classify_error(text, status) == expected, (text, status)

    # None on success, regardless of content
    assert classify_error("anything at all", "success") is None
    assert classify_error("", "error") == "other"


def test_messages_jsonl_log_and_mirror(tmp_path: Path):
    logger = TraceLogger(tmp_path, task="Messages Task")
    # Touched (created empty) in __init__
    assert (tmp_path / "messages.jsonl").exists()
    assert (tmp_path / "messages.jsonl").read_text(encoding="utf-8") == ""

    logger.log_message(role="system", content="sys prompt", turn=0)
    logger.log_message(role="user", content="hello", turn=0)
    logger.log_message(
        role="assistant",
        content="working on it",
        turn=1,
        tool_calls=[{"id": "c1", "name": "translate_text", "arguments": {"text": "hi"}}],
    )

    lines = _read_jsonl(tmp_path / "messages.jsonl")
    assert [m["seq"] for m in lines] == [1, 2, 3]
    assert [m["role"] for m in lines] == ["system", "user", "assistant"]
    assert [m["turn"] for m in lines] == [0, 0, 1]
    assert lines[1]["content"] == "hello"
    assert lines[2]["tool_calls"][0]["name"] == "translate_text"
    assert lines[2]["tool_calls"][0]["arguments"] == {"text": "hi"}
    assert lines[2]["tool_calls"][0]["id"] == "c1"

    logger.log_step(
        step=1,
        plan="p",
        tool="t",
        arguments={},
        result_summary="s",
        latency_ms=1,
        cost_inr=0.0,
        cumulative_cost_inr=0.0,
    )
    logger.write_report_and_result(
        task="Messages Task",
        status="completed",
        final_answer="done",
        cost_ledger=CostLedger(max_budget_inr=5.0),
    )
    assert logger.archive_dir is not None
    mirrored = _read_jsonl(logger.archive_dir / "messages.jsonl")
    assert [m["content"] for m in mirrored] == ["sys prompt", "hello", "working on it"]


def test_messages_reset_between_sequential_runs(tmp_path: Path):
    run1 = TraceLogger(tmp_path, task="Run A")
    run1.log_message(role="user", content="run1 msg")
    _log_basic_step(run1, step=1)
    run1.write_report_and_result(
        task="Run A",
        status="completed",
        final_answer="a",
        cost_ledger=CostLedger(max_budget_inr=5.0),
    )

    # Run 2 on the same directory: messages.jsonl must start empty, no contamination
    run2 = TraceLogger(tmp_path, task="Run B")
    assert _read_jsonl(tmp_path / "messages.jsonl") == []
    run2.log_message(role="user", content="run2 msg")
    lines = _read_jsonl(tmp_path / "messages.jsonl")
    assert [m["content"] for m in lines] == ["run2 msg"]
    # seq restarts at 1 for the new run
    assert [m["seq"] for m in lines] == [1]


def test_llm_spans_record_and_parent_linkage(tmp_path: Path):
    logger = TraceLogger(tmp_path, task="LLM Spans Task")
    assert (tmp_path / "llm_spans.jsonl").exists()

    tc = _fake_tool_call("call_1", "translate_text", {"text": "hi"})
    span_id = logger.log_llm_span(
        step=1,
        plan="Translate the text",
        model="gpt-4o",
        latency_ms=42,
        prompt_tokens=10,
        completion_tokens=5,
        finish_reason="tool_calls",
        tool_calls=[tc],
    )

    spans = _read_jsonl(tmp_path / "llm_spans.jsonl")
    assert len(spans) == 1
    s = spans[0]
    assert s["schema_version"] == 2
    assert s["run_id"] == logger.run_id
    assert s["span_id"] == span_id
    assert s["span_type"] == "llm"
    assert s["step"] == 1
    assert s["turn"] == 1
    assert s["prompt_tokens"] == 10
    assert s["completion_tokens"] == 5
    assert s["finish_reason"] == "tool_calls"
    assert s["tool_calls"] == [{"name": "translate_text", "arguments": {"text": "hi"}}]
    assert s["error_class"] is None
    assert s["error"] is None

    # Same-step tool span links to the LLM span
    rec_same = _log_basic_step(logger, step=1)
    assert rec_same.parent_span_id == span_id
    # Different step → no parent
    rec_other = _log_basic_step(logger, step=2)
    assert rec_other.parent_span_id is None

    # Error LLM spans still participate in parent linkage
    err_span = logger.log_llm_span(
        step=3, plan="failed call", model="gpt-4o", latency_ms=7,
        error_class="other", error="boom",
    )
    err_rec = _read_jsonl(tmp_path / "llm_spans.jsonl")[-1]
    assert err_rec["error_class"] == "other"
    assert err_rec["error"] == "boom"
    rec_after = _log_basic_step(logger, step=3)
    assert rec_after.parent_span_id == err_span

    # llm_spans.jsonl mirrored into the run archive
    logger.write_report_and_result(
        task="LLM Spans Task",
        status="completed",
        final_answer="done",
        cost_ledger=CostLedger(max_budget_inr=5.0),
    )
    assert logger.archive_dir is not None
    mirrored = _read_jsonl(logger.archive_dir / "llm_spans.jsonl")
    assert [r["span_id"] for r in mirrored] == [span_id, err_span]


def test_llm_spans_accept_parsed_dict_tool_calls(tmp_path: Path):
    logger = TraceLogger(tmp_path)
    logger.log_llm_span(
        step=0,
        plan="p",
        model="m",
        latency_ms=1,
        tool_calls=[{"function": {"name": "submit_plan", "arguments": json.dumps({"goal": "g"})}}],
    )
    s = _read_jsonl(tmp_path / "llm_spans.jsonl")[0]
    assert s["tool_calls"] == [{"name": "submit_plan", "arguments": {"goal": "g"}}]


def test_report_metadata_roundtrip(tmp_path: Path):
    logger = TraceLogger(tmp_path)
    metadata = {"config": {"llm_model": "x"}, "git_commit": None, "input_files": []}
    logger.write_report_and_result(
        task="T",
        status="completed",
        final_answer="a",
        cost_ledger=CostLedger(max_budget_inr=5.0),
        metadata=metadata,
    )
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["metadata"] == metadata


def test_report_metadata_defaults_empty(tmp_path: Path):
    logger = TraceLogger(tmp_path)
    logger.write_report_and_result(
        task="T",
        status="completed",
        final_answer="a",
        cost_ledger=CostLedger(max_budget_inr=5.0),
    )
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["metadata"] == {}


def test_rotation_archives_spans_and_messages_from_crashed_run(tmp_path: Path):
    # Simulate a crashed previous run: stale spans/, messages.jsonl, trace.jsonl
    spans_dir = tmp_path / "spans"
    spans_dir.mkdir(parents=True, exist_ok=True)
    (spans_dir / "s0001_translate_text-output.txt").write_text("stale span output", encoding="utf-8")
    (tmp_path / "messages.jsonl").write_text(
        json.dumps({"seq": 1, "role": "user", "content": "stale message"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "trace.jsonl").write_text(
        json.dumps({
            "step": 1,
            "timestamp": "2026-11-18T10:00:00Z",
            "plan": "Crashed",
            "tool": "t",
            "arguments": {},
            "result_summary": "s",
            "latency_ms": 1,
            "cost_inr": 0.0,
            "cumulative_cost_inr": 0.0,
            "status": "success",
        }) + "\n",
        encoding="utf-8",
    )

    logger = TraceLogger(tmp_path, task="Fresh Run")

    # Stale files archived into runs/
    crash_archive = None
    for r in (tmp_path / "runs").iterdir():
        if (r / "spans" / "s0001_translate_text-output.txt").exists():
            crash_archive = r
    assert crash_archive is not None
    assert (crash_archive / "spans" / "s0001_translate_text-output.txt").read_text(
        encoding="utf-8"
    ) == "stale span output"
    stale_messages = _read_jsonl(crash_archive / "messages.jsonl")
    assert [m["content"] for m in stale_messages] == ["stale message"]
    assert (crash_archive / "trace.jsonl").exists()

    # Cleaned for the fresh run
    assert list((tmp_path / "spans").iterdir()) == []
    assert (tmp_path / "messages.jsonl").read_text(encoding="utf-8") == ""
    assert (tmp_path / "llm_spans.jsonl").read_text(encoding="utf-8") == ""


def test_mirroring_includes_spans_directory(tmp_path: Path):
    logger = TraceLogger(tmp_path, task="Spans Mirror Task")
    _log_basic_step(logger, step=1, full_output="some full output")
    logger.write_report_and_result(
        task="Spans Mirror Task",
        status="completed",
        final_answer="done",
        cost_ledger=CostLedger(max_budget_inr=5.0),
    )
    assert logger.archive_dir is not None
    archived_spans = list((logger.archive_dir / "spans").iterdir())
    assert len(archived_spans) == 1
    assert archived_spans[0].read_text(encoding="utf-8") == "some full output"


def test_trace_records_are_json_serializable(tmp_path: Path):
    logger = TraceLogger(tmp_path)
    tc = _fake_tool_call("call_9", "translate_text", {"text": "héllo"})
    logger.log_llm_span(step=1, plan="p", model="m", latency_ms=1, tool_calls=[tc])
    _log_basic_step(logger, step=1, full_output="full output ✓ with unicode")

    # Every line in every trace file parses; record.__dict__ serializes
    for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        json.loads(line)
    for line in (tmp_path / "llm_spans.jsonl").read_text(encoding="utf-8").splitlines():
        json.loads(line)
    for record in logger.steps:
        json.dumps(record.__dict__, ensure_ascii=False)


def test_config_snapshot_is_redacted():
    cfg = BodhanConfig(
        transcribe_key="bodhan-secret",
        translate_key="",
        speak_key="",
        ocr_key="",
        base_url="https://api.example/v1",
        llm_base_url="https://llm.example/v1",
        llm_model="gpt-x",
        llm_api_key="llm-secret",
        llm_reasoning_effort="",
    )
    snap = cfg.snapshot()
    assert snap["base_url"] == "https://api.example/v1"
    assert snap["llm_base_url"] == "https://llm.example/v1"
    assert snap["llm_model"] == "gpt-x"
    assert snap["llm_reasoning_effort"] is None
    assert snap["has_bodhan_keys"] is True
    assert snap["has_llm_key"] is True
    # No key material leaks
    flat = json.dumps(snap)
    assert "secret" not in flat

    empty = BodhanConfig().snapshot()
    assert empty["has_bodhan_keys"] is False
    assert empty["has_llm_key"] is False
    assert empty["llm_reasoning_effort"] is None


# ---------------------------------------------------------------------------
# D1–D3 fix-pass tests (additive)
# ---------------------------------------------------------------------------

def test_classify_error_anchored_refusal_and_network_markers():
    # D1: transport errors containing "refused" must NOT classify as policy_refusal
    assert classify_error("ToolError: connection refused (ConnectionError)", "error") == "network"
    assert classify_error("Request refused by gateway; pre-flight estimate unavailable", "error") == "preflight"
    assert classify_error("Bodhan service may be down (connection refused)", "error") == "network"
    # Bare "refuse" is no longer a policy marker; unanchored refusals land on "other"
    assert classify_error("we refuse this request", "error") == "other"
    # Anchored policy markers still classify as policy_refusal
    assert classify_error("refused by policy", "error") == "policy_refusal"
    assert classify_error("policy: something disallowed", "error") == "policy_refusal"


def test_spill_multibyte_output_is_strict_utf8_under_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_TRACE_MAX_OUTPUT_BYTES", "100")
    logger = TraceLogger(tmp_path)
    content = "तमिलம்" * 50  # multibyte (3-byte) chars only, 900 bytes total

    rec = _log_basic_step(logger, step=1, full_output=content)

    span_file = next((tmp_path / "spans").iterdir())
    assert span_file.stat().st_size <= 100
    # Strict decode: no orphaned partial UTF-8 sequence at the cap boundary
    with open(span_file, "r", encoding="utf-8", errors="strict") as f:
        text = f.read()
    assert text.endswith("\n...[truncated]")
    assert rec.output_chars == len(content)
    assert rec.output_sha256 == hashlib.sha256(span_file.read_bytes()).hexdigest()


def test_early_crash_archival_of_messages_and_spans(tmp_path: Path):
    # Simulate a run that crashed after logging messages/spans but BEFORE the
    # first log_step: trace.jsonl empty, report.json absent.
    (tmp_path / "trace.jsonl").write_text("", encoding="utf-8")
    messages_file = tmp_path / "messages.jsonl"
    messages_file.write_text(
        json.dumps({"seq": 1, "role": "user", "content": "early one"}) + "\n"
        + json.dumps({"seq": 2, "role": "assistant", "content": "early two"}) + "\n",
        encoding="utf-8",
    )
    spans_dir = tmp_path / "spans"
    spans_dir.mkdir(parents=True, exist_ok=True)
    (spans_dir / "s0001_translate_text-output.txt").write_text("early span output", encoding="utf-8")
    assert not (tmp_path / "report.json").exists()

    logger = TraceLogger(tmp_path, task="Fresh Run After Early Crash")

    # Crash archive contains the early-crash data
    crash_archive = None
    for r in (tmp_path / "runs").iterdir():
        if (r / "messages.jsonl").exists():
            crash_archive = r
    assert crash_archive is not None, "early-crash messages/spans were not archived"
    archived_messages = _read_jsonl(crash_archive / "messages.jsonl")
    assert [m["content"] for m in archived_messages] == ["early one", "early two"]
    assert (crash_archive / "spans" / "s0001_translate_text-output.txt").read_text(
        encoding="utf-8"
    ) == "early span output"

    # Fresh run: everything reset, message seq restarts at 1
    assert messages_file.read_text(encoding="utf-8") == ""
    assert _read_jsonl(tmp_path / "llm_spans.jsonl") == []
    assert list(spans_dir.iterdir()) == []
    logger.log_message(role="user", content="first message of fresh run")
    assert [m["seq"] for m in _read_jsonl(messages_file)] == [1]
