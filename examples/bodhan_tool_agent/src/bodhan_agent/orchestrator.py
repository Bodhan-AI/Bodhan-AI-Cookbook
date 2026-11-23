"""Orchestrator: coordinates task understanding, planning, execution, verification, and reporting."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from bodhan_agent.budget import BudgetManager
from bodhan_agent.cache import ResultCache
from bodhan_agent.checkpoint import CheckpointManager
from bodhan_agent.executor import PlanExecutor
from bodhan_agent.file_inspector import detect_prompt_injection
from bodhan_agent.llm import LLMClient
from bodhan_agent.models import ExecutionPlan, TaskSpec, TraceEvent, VerificationResult
from bodhan_agent.planner import TaskPlanner
from bodhan_agent.trace import TraceLogger
from bodhan_agent.verifier import Verifier
from bodhan_mcp.errors import PromptInjectionError

logger = logging.getLogger("bodhan_agent.orchestrator")


class AgentOrchestrator:
    """End-to-end task runner managing the full lifecycle of an autonomous task execution."""

    def __init__(
        self,
        out_dir: Path,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        llm: LLMClient | None = None,
    ):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = self.out_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.budget = BudgetManager(max_steps=max_steps, max_cost_inr=max_cost_inr)
        self.cache = ResultCache(cache_dir=self.out_dir / ".cache")
        self.trace_logger = TraceLogger(out_dir=self.out_dir)
        self.checkpoint = CheckpointManager(out_dir=self.out_dir)
        self.llm = llm or LLMClient()
        self.planner = TaskPlanner(llm_client=self.llm)
        self.executor = PlanExecutor(
            budget=self.budget,
            cache=self.cache,
            trace_logger=self.trace_logger,
            checkpoint=self.checkpoint,
            llm=self.llm,
        )

    def run(
        self,
        task_prompt: str,
        files_path: str | Path | None = None,
        resume_task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute an autonomous task from start to finish."""
        t_start = time.time()
        logger.info(f"Initiating task: '{task_prompt}'")

        # Check prompt injection in user prompt
        if detect_prompt_injection(task_prompt):
            err_msg = "Security alert: Malicious prompt injection detected in user task."
            self.trace_logger.record_event(
                TraceEvent(task_id="sec_alert", event_type="error", status="rejected", result_summary=err_msg)
            )
            raise PromptInjectionError(err_msg, snippet=task_prompt[:100])

        # 1. Task Understanding & Inspection
        task_spec: TaskSpec = self.planner.parse_task(task_prompt, files_path=files_path)

        # Log task start
        self.trace_logger.record_event(
            TraceEvent(
                task_id=task_spec.task_id,
                event_type="task_start",
                status="started",
                result_summary=f"Parsed task: {task_prompt}",
            )
        )

        # Check for resume
        plan: ExecutionPlan | None = None
        if resume_task_id:
            ckpt = self.checkpoint.load_checkpoint(expected_task_id=resume_task_id)
            if ckpt:
                logger.info(f"Resuming existing task {resume_task_id} from checkpoint...")
                plan = self.checkpoint.restore_plan(ckpt)
                self.budget.current_cost_inr = ckpt.get("spent_cost_inr", 0.0)

        # 2. Planning (if not resumed)
        if not plan:
            plan = self.planner.create_plan(
                task_spec=task_spec,
                out_dir=self.out_dir,
                max_steps=self.budget.max_steps,
                max_cost_inr=self.budget.max_cost_inr,
            )

        # 3. Execution
        self.executor.execute_plan(
            task_id=task_spec.task_id,
            user_prompt=task_prompt,
            plan=plan,
        )

        # 4. Mandatory Verification
        verifications: list[VerificationResult] = []
        generated_artifacts: list[str] = []

        # Find all artifacts in artifacts directory
        for art in self.artifacts_dir.iterdir():
            if art.is_file() and not art.name.startswith("."):
                generated_artifacts.append(str(art))
                # Verify existence and size
                verifications.append(Verifier.verify_file_artifact(art))
                # Verify audio if WAV
                if art.suffix.lower() == ".wav":
                    verifications.append(Verifier.verify_audio_duration(art))
                # Verify subtitles if SRT
                elif art.suffix.lower() == ".srt":
                    verifications.append(Verifier.verify_srt_subtitles(art.read_text(encoding="utf-8", errors="ignore")))

        # Check step-level OCR verifications
        for s in plan.steps:
            if s.tool_name == "extract_text_from_document" and s.result_data:
                res = s.result_data
                verifications.append(
                    Verifier.verify_ocr_pages(
                        total_pages=res.get("total_pages", 1),
                        successful_pages=res.get("successful_pages", 0),
                        failed_pages=res.get("failed_pages", 0),
                    )
                )

        # Determine overall task status
        failed_steps = [s for s in plan.steps if s.status.value == "failed"]
        if len(plan.steps) == 0:
            overall_status = "failed"
            error_message = "No executable steps formulated; missing input files or invalid task request."
        elif len(failed_steps) == len(plan.steps):
            overall_status = "failed"
            error_message = failed_steps[0].error_message
        elif len(failed_steps) > 0:
            overall_status = "partial"
            error_message = f"{len(failed_steps)} steps encountered issues: {failed_steps[0].error_message}"
        else:
            overall_status = "completed"
            error_message = None

        total_runtime = time.time() - t_start

        # 5. Generate Final Traceability Reports
        self.trace_logger.generate_final_report(
            task_spec=task_spec,
            plan=plan,
            verifications=verifications,
            total_actual_cost_inr=self.budget.current_cost_inr,
            total_runtime_s=total_runtime,
            status=overall_status,
            error_message=error_message,
            generated_artifacts=generated_artifacts,
            assumptions=[
                "Speech synthesis generates 24 kHz mono PCM16 audio",
                "Non-Indic target language translations (e.g. French) are rejected by Bodhan API",
                "Audio clips > 28s are chunked sequentially to prevent API timeouts",
            ],
        )

        return {
            "task_id": task_spec.task_id,
            "status": overall_status,
            "cost_inr": self.budget.current_cost_inr,
            "runtime_s": total_runtime,
            "artifacts": generated_artifacts,
            "verifications": [v.model_dump() for v in verifications],
            "error": error_message,
        }
