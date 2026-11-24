"""Unit tests for cost and step limits, impossible task handling, and output verifications."""

import json
import time
from pathlib import Path

import pytest

from bodhan_agent.budget import (
    BudgetTracker,
    RupeeBudgetExceededError,
    StepBudgetExhaustedError,
    TimeoutBudgetExceededError,
)
from bodhan_agent.models import ExecutionStatus
from bodhan_agent.planner import plan_task, rule_based_deterministic_plan
from bodhan_agent.runner import AgentRunner
from bodhan_agent.verifier import (
    check_script_match,
    check_synthesized_audio,
    check_translation_quality,
)


def test_step_budget_exhaustion():
    """Agent cleanly halts when the step budget limit is reached."""
    tracker = BudgetTracker(max_steps=3, max_cost_inr=20.0, timeout_seconds=60.0)

    assert tracker.record_step_start() == 1
    assert tracker.record_step_start() == 2
    assert tracker.record_step_start() == 3

    with pytest.raises(StepBudgetExhaustedError) as exc_info:
        tracker.record_step_start()

    assert "maximum step budget of 3" in str(exc_info.value)


def test_rupee_cost_budget_exceeded():
    """Agent halts when monetary INR cost reaches or exceeds the rupee budget."""
    tracker = BudgetTracker(max_steps=10, max_cost_inr=1.50, timeout_seconds=60.0)

    # First call within budget: ₹0.80
    tracker.check_cost_safety(0.80)
    tracker.record_cost("transcribe", 0.80)
    assert tracker.current_cost_inr == 0.80

    # Second call would exceed ₹1.50: ₹0.80 + ₹0.80 = ₹1.60
    with pytest.raises(RupeeBudgetExceededError):
        tracker.check_cost_safety(0.80)


def test_timeout_budget_enforcement():
    """Agent halts when execution time exceeds timeout budget."""
    tracker = BudgetTracker(max_steps=10, max_cost_inr=20.0, timeout_seconds=0.05)
    time.sleep(0.08)

    with pytest.raises(TimeoutBudgetExceededError):
        tracker.check_timeout()


def test_verifier_indic_script_detection():
    """Verifier identifies valid Indian language scripts and flags mismatched scripts."""
    hindi_text = "यह एक मान्य हिंदी वाक्य है।"
    res_hi = check_script_match(hindi_text, "hi")
    assert res_hi.passed is True
    assert res_hi.score > 0.5

    # English text claiming to be Hindi should fail native script check
    ascii_text = "This is pure english text without any devanagari characters."
    res_fail = check_script_match(ascii_text, "hi")
    assert res_fail.passed is False

    # Kannada text
    kannada_text = "ಇದು ಮಾನ್ಯವಾದ ಕನ್ನಡ ಪಠ್ಯವಾಗಿದೆ."
    res_kn = check_script_match(kannada_text, "kn")
    assert res_kn.passed is True


def test_verifier_translation_quality():
    """Verifier assesses non-emptiness, length ratio, and markdown structure."""
    src = "# Education\n\nIndian languages are essential for accessible education."
    tgt = "# ಶಿಕ್ಷಣ\n\nಭಾರತೀಯ ಭಾಷೆಗಳು ಪ್ರವೇಶಿಸಬಹುದಾದ ಶಿಕ್ಷಣಕ್ಕೆ ಅತ್ಯಗತ್ಯ."

    results = check_translation_quality(src, tgt, "kn")
    checks = {r.check_name: r.passed for r in results}

    assert checks["non_empty_check"] is True
    assert checks["length_ratio_check"] is True
    assert checks["structure_heading_check"] is True


def test_verifier_audio_companion_metadata(tmp_path):
    """Synthesized audio must exist, have plausible duration, and include AI disclaimer metadata."""
    audio_file = tmp_path / "speech.wav"
    audio_file.write_bytes(b"RIFF" + b"\x00" * 4000)  # dummy audio bytes

    # Without companion metadata
    results = check_synthesized_audio(audio_file, expected_char_count=50)
    assert any(not r.passed and r.check_name == "ai_label_check" for r in results)

    # With valid companion metadata
    meta_file = audio_file.with_suffix(".json")
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump({"ai_generated": True, "model": "indic-speak"}, f)

    results2 = check_synthesized_audio(audio_file, expected_char_count=50)
    label_check = next(r for r in results2 if r.check_name == "ai_label_check")
    assert label_check.passed is True


@pytest.mark.asyncio
async def test_impossible_task_unsupported_language(tmp_path):
    """Impossible task requesting an unsupported language halts cleanly with IMPOSSIBLE status."""
    runner = AgentRunner(
        task="Translate speech audio into French and German",
        files=[],
        output_dir=tmp_path / "run_impossible_lang",
    )
    report = await runner.run()

    assert report.status == ExecutionStatus.IMPOSSIBLE
    assert "not supported by Bodhan Indic models" in report.reason
    assert report.steps_taken == 0
    assert report.total_cost_inr == 0.0

    # Ensure result.md and report.json exist and honestly state impossibility
    result_md = (tmp_path / "run_impossible_lang" / "result.md").read_text(encoding="utf-8")
    assert "IMPOSSIBLE TASK" in result_md


@pytest.mark.asyncio
async def test_impossible_task_missing_file(tmp_path):
    """Impossible task with non-existent file halts cleanly without fabrication."""
    runner = AgentRunner(
        task="Transcribe lecture audio",
        files=[str(tmp_path / "ghost_audio_file.wav")],
        output_dir=tmp_path / "run_missing_file",
    )
    report = await runner.run()

    assert report.status == ExecutionStatus.IMPOSSIBLE
    assert "does not exist" in report.reason
    assert report.steps_taken == 0
