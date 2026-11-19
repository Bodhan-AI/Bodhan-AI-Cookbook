from __future__ import annotations

import json
from pathlib import Path

from bodhan_agent.eval_suite import run_evaluation_suite


def test_end_to_end_evaluation_suite(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    summary = run_evaluation_suite(out_dir, use_mock_if_no_keys=True)

    assert summary["total_tasks"] == 8
    assert summary["passed_tasks"] == 8
    assert summary["pass_rate_percent"] == 100.0

    t1_dir = out_dir / "task_01_tamil_audio_summary"
    assert (t1_dir / "result.md").exists()
    assert (t1_dir / "trace.jsonl").exists()
    assert (t1_dir / "report.json").exists()

    t1_report = json.loads((t1_dir / "report.json").read_text(encoding="utf-8"))
    assert t1_report["status"] == "completed"
    assert t1_report["quality_targets"]["all_outputs_verified"] is True
    assert len(t1_report["verification_results"]) >= 2

    trace_lines = [
        json.loads(line) for line in (t1_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    tools_called = [step["tool"] for step in trace_lines]
    assert "inspect_media_and_files" in tools_called
    assert "extract_document_text" in tools_called
    assert "translate_text" in tools_called
    assert "synthesize_speech" in tools_called
    assert "verify_translation_quality" in tools_called
    assert "verify_audio_quality" in tools_called

    t6_report = json.loads((out_dir / "task_06_impossible_french" / "report.json").read_text(encoding="utf-8"))
    assert t6_report["status"] == "impossible_task"
    assert t6_report["artifacts"] == []
