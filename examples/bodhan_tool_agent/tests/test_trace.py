"""Unit tests for the TraceLogger and Report Generator (src.bodhan_agent.trace)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.bodhan_agent.trace import TraceLogger, slugify
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
