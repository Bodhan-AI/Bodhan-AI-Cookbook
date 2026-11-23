"""Checkpointing and resumption manager for interrupted workflows."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from bodhan_agent.models import ExecutionPlan

logger = logging.getLogger("bodhan_agent.checkpoint")


class CheckpointManager:
    """Saves task execution checkpoints and restores completed state for task resumption."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_file = self.out_dir / "checkpoint.json"

    def save_checkpoint(
        self,
        task_id: str,
        user_prompt: str,
        plan: ExecutionPlan,
        spent_cost_inr: float,
        step_index: int,
    ):
        """Save current execution state to disk."""
        data = {
            "task_id": task_id,
            "user_prompt": user_prompt,
            "plan": plan.model_dump(),
            "spent_cost_inr": spent_cost_inr,
            "last_step_index": step_index,
        }
        try:
            self.checkpoint_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write checkpoint: {e}")

    def load_checkpoint(self, expected_task_id: str | None = None) -> dict[str, Any] | None:
        """Load and validate an existing checkpoint."""
        if not self.checkpoint_file.exists():
            return None

        try:
            data = json.loads(self.checkpoint_file.read_text(encoding="utf-8"))
            if expected_task_id and data.get("task_id") != expected_task_id:
                logger.warning(
                    f"Checkpoint task_id mismatch: expected {expected_task_id}, found {data.get('task_id')}"
                )
                return None
            return data
        except Exception as e:
            logger.error(f"Failed to read checkpoint: {e}")
            return None

    def restore_plan(self, checkpoint_data: dict[str, Any]) -> ExecutionPlan:
        """Reconstruct ExecutionPlan from checkpoint payload."""
        plan_dict = checkpoint_data["plan"]
        return ExecutionPlan.model_validate(plan_dict)
