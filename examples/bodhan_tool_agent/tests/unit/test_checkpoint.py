"""Unit tests for task state serialization and checkpoint resumption."""

from __future__ import annotations

from pathlib import Path
from bodhan_agent.checkpoint import CheckpointManager
from bodhan_agent.models import ExecutionPlan, PlanStep, StepStatus


def test_checkpoint_save_and_restore(tmp_path: Path):
    cm = CheckpointManager(out_dir=tmp_path)
    step1 = PlanStep(
        step_id="step_01",
        description="First step",
        tool_name="transcribe_audio",
        tool_arguments={"file_path": "clip.wav"},
        status=StepStatus.COMPLETED,
        result_data={"text": "transcribed speech"},
    )
    step2 = PlanStep(
        step_id="step_02",
        description="Second step",
        tool_name="translate_text",
        tool_arguments={"text": "transcribed speech", "target_language": "ta"},
        status=StepStatus.PENDING,
    )
    plan = ExecutionPlan(task_id="task_ckpt_test", steps=[step1, step2], estimated_total_cost_inr=0.20)

    cm.save_checkpoint(
        task_id="task_ckpt_test",
        user_prompt="Translate audio to Tamil",
        plan=plan,
        spent_cost_inr=0.10,
        step_index=0,
    )

    data = cm.load_checkpoint(expected_task_id="task_ckpt_test")
    assert data is not None
    assert data["task_id"] == "task_ckpt_test"
    assert data["spent_cost_inr"] == 0.10

    restored = cm.restore_plan(data)
    assert len(restored.steps) == 2
    assert restored.steps[0].status == StepStatus.COMPLETED
    assert restored.steps[1].status == StepStatus.PENDING
