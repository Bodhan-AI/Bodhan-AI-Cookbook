"""Tests for direct text input via --text parameter."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from bodhan_agent import loop
from bodhan_agent.state import TaskStatus, PlanStep, StepStatus


@pytest.mark.asyncio
async def test_loop_run_with_direct_text(tmp_path: Path):
    """Providing direct text creates an input text file and executes normally."""
    sample_text = "Good morning, have a wonderful day."
    out_dir = tmp_path / "out_text"

    # Mock planner and executor to verify workflow with direct text
    with patch("bodhan_agent.planner.interpret_task") as mock_interpret, \
         patch("bodhan_agent.planner.make_plan") as mock_plan, \
         patch("bodhan_agent.executor.execute_step") as mock_exec:

        mock_obj = MagicMock(
            goal="Translate input text to Bengali",
            deliverables=["translated text"],
            languages=["bn"],
            assumptions=[],
            feasible=True,
            reason="Supported",
        )
        mock_obj.model_dump.return_value = {
            "goal": "Translate input text to Bengali",
            "deliverables": ["translated text"],
            "languages": ["bn"],
            "assumptions": [],
            "feasible": True,
            "reason": "Supported",
        }
        mock_interpret.return_value = mock_obj
        mock_plan.return_value = [
            PlanStep(
                id="step_1",
                tool="translate_text",
                args={"text": sample_text, "target_language": "bn"},
                purpose="Translate text",
                expected_output="Bengali text",
            )
        ]

        async def fake_execute(step, state, *args, **kwargs):
            step.status = StepStatus.DONE
            step.result = {"ok": True, "translated_text": "সুপ্রভাত", "output_file_path": str(out_dir / "artifacts" / "trans.md")}

        mock_exec.side_effect = fake_execute

        state = await loop.run(
            task="Translate this into Bengali",
            text=sample_text,
            out_dir=str(out_dir),
        )

        assert state.status == TaskStatus.COMPLETED
        assert len(state.files) == 1
        assert state.files[0].name == "input_text.txt"
        assert state.files[0].type == "text"
        assert (out_dir / "artifacts" / "input_text.txt").exists()
        assert (out_dir / "artifacts" / "input_text.txt").read_text(encoding="utf-8") == sample_text


def test_cli_requires_files_or_text():
    """CLI exits with error code 2 if neither --files nor --text is supplied."""
    from bodhan_agent.__main__ import main
    import sys

    with patch.object(sys, "argv", ["bodhan_agent", "--task", "Sample task"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
