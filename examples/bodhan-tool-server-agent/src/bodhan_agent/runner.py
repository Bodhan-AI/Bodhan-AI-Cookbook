"""Main Execution Loop for Bodhan Task Agent.

Coordinates:
- Planning and task decomposition
- Tool execution via dispatch layer
- Output verification checks
- Resource and monetary budget enforcement
- Dossier generation (result.md, report.json, trace.jsonl, artifacts/)
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from bodhan_agent.budget import (
    BudgetTracker,
    RupeeBudgetExceededError,
    StepBudgetExhaustedError,
    TimeoutBudgetExceededError,
)
from bodhan_agent.models import (
    ExecutionStatus,
    Plan,
    PlanStep,
    RunReport,
    StepTrace,
    VerificationResult,
)
from bodhan_agent.planner import plan_task
from bodhan_agent.reporter import AgentReporter
from bodhan_agent.verifier import (
    check_ocr_quality,
    check_synthesized_audio,
    check_transcription_quality,
    check_translation_quality,
)
from bodhan_mcp.server import dispatch_tool_call

logger = logging.getLogger("bodhan.agent.runner")


class AgentRunner:
    """Executes multi-step language tasks with full verification and budget tracking."""

    def __init__(
        self,
        task: str,
        files: List[str],
        output_dir: Union[str, Path],
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        timeout_seconds: float = 300.0,
    ):
        self.task = task
        self.files = files
        self.output_dir = Path(output_dir).resolve()
        self.reporter = AgentReporter(self.output_dir)
        self.budget = BudgetTracker(
            max_steps=max_steps,
            max_cost_inr=max_cost_inr,
            timeout_seconds=timeout_seconds,
        )
        self.traces: List[StepTrace] = []
        self.verification_results: List[VerificationResult] = []
        self.artifacts: List[str] = []

    async def run(self) -> RunReport:
        """Execute the task pipeline from planning through verification and reporting."""
        logger.info("Initiating agent run for task: '%s'", self.task)

        # Step 1: Planning phase
        plan = await plan_task(self.task, self.files)

        # Check for immediate impossibility (unsupported languages, missing files)
        if not plan.is_feasible:
            report = RunReport(
                task=self.task,
                status=ExecutionStatus.IMPOSSIBLE,
                reason=plan.impossibility_reason or "Task cannot be completed within Bodhan constraints.",
                steps_taken=0,
                max_steps=self.budget.max_steps,
                total_cost_inr=0.0,
                max_cost_inr=self.budget.max_cost_inr,
                cost_breakdown={},
                duration_seconds=self.budget.elapsed_seconds,
                input_files=self.files,
                artifacts=[],
                verification_results=[],
                plan=plan,
                final_summary="Task halted honestly as impossible; no fake outputs generated.",
            )
            self.reporter.write_report_json(report)
            self.reporter.write_result_md(report, self.traces)
            return report

        # Context pipe carrying outputs from earlier steps to downstream steps
        context_data: Dict[str, Any] = {}
        status = ExecutionStatus.SUCCESS
        reason = None

        try:
            for step in plan.steps:
                step_num = self.budget.record_step_start()
                start_step_time = time.time()
                tool_args = dict(step.arguments)

                # Feedforward context into downstream tools
                if step.tool == "bodhan_translate":
                    if not tool_args.get("text") and not tool_args.get("text_path"):
                        # Pipe from transcript or OCR output
                        piped_text = context_data.get("transcript") or context_data.get("ocr_text")
                        if piped_text:
                            tool_args["text"] = piped_text

                elif step.tool == "bodhan_speak":
                    if not tool_args.get("text") and not tool_args.get("text_path"):
                        # Pipe from translation or transcript
                        piped_text = context_data.get("translated_text") or context_data.get("transcript")
                        if piped_text:
                            tool_args["text"] = piped_text

                    # Assign output audio file inside run artifacts directory
                    if not tool_args.get("output_path"):
                        audio_filename = f"synthesized_step{step_num}.wav"
                        tool_args["output_path"] = str(self.reporter.artifacts_dir / audio_filename)

                # Execute tool call
                tool_res = await dispatch_tool_call(step.tool, tool_args)
                cost = float(tool_res.get("cost_inr", 0.0))
                self.budget.record_cost(step.tool, cost)
                duration = round(time.time() - start_step_time, 2)

                # Update context
                if "transcript" in tool_res:
                    context_data["transcript"] = tool_res["transcript"]
                    # Save transcript artifact
                    tf = self.reporter.artifacts_dir / f"transcript_step{step_num}.txt"
                    with open(tf, "w", encoding="utf-8") as f:
                        f.write(tool_res["transcript"])
                    self.artifacts.append(str(tf))

                if "translated_text" in tool_res:
                    context_data["translated_text"] = tool_res["translated_text"]
                    # Save translation artifact
                    tf = self.reporter.artifacts_dir / f"translation_step{step_num}.txt"
                    with open(tf, "w", encoding="utf-8") as f:
                        f.write(tool_res["translated_text"])
                    self.artifacts.append(str(tf))

                if "audio_path" in tool_res and Path(tool_res["audio_path"]).exists():
                    audio_p = Path(tool_res["audio_path"])
                    if audio_p.parent != self.reporter.artifacts_dir:
                        dest = self.reporter.artifacts_dir / audio_p.name
                        shutil.copy2(audio_p, dest)
                        self.artifacts.append(str(dest))
                    else:
                        self.artifacts.append(str(audio_p))

                if "text" in tool_res and step.tool == "bodhan_ocr":
                    context_data["ocr_text"] = tool_res["text"]
                    # Save OCR markdown artifact
                    of = self.reporter.artifacts_dir / f"ocr_step{step_num}.md"
                    with open(of, "w", encoding="utf-8") as f:
                        f.write(tool_res["text"])
                    self.artifacts.append(str(of))

                # Step Verification
                step_verifications: List[VerificationResult] = []
                if step.tool == "bodhan_translate":
                    src = tool_args.get("text", "")
                    tgt = tool_res.get("translated_text", "")
                    step_verifications = check_translation_quality(src, tgt, tool_args.get("target_lang", "hi"))
                elif step.tool == "bodhan_speak":
                    audio_target = tool_res.get("audio_path", "")
                    step_verifications = check_synthesized_audio(audio_target, len(tool_args.get("text", "")))
                elif step.tool == "bodhan_transcribe":
                    step_verifications = check_transcription_quality(tool_res.get("transcript", ""))
                elif step.tool == "bodhan_ocr":
                    step_verifications = check_ocr_quality(tool_res.get("text", ""), tool_res.get("page_count", 1))

                self.verification_results.extend(step_verifications)
                all_passed = all(v.passed for v in step_verifications)

                # Log trace
                trace = StepTrace(
                    step_number=step_num,
                    tool=step.tool,
                    arguments={k: (str(v) if isinstance(v, Path) else v) for k, v in tool_args.items()},
                    status="SUCCESS" if all_passed else "VERIFICATION_WARNING",
                    output=tool_res,
                    cost_inr=cost,
                    duration_seconds=duration,
                    verification_passed=all_passed,
                    verification_notes="; ".join(v.details for v in step_verifications),
                    timestamp=datetime.datetime.now().isoformat(),
                )
                self.traces.append(trace)
                self.reporter.log_trace(trace)

        except StepBudgetExhaustedError as e:
            status = ExecutionStatus.BUDGET_EXCEEDED
            reason = str(e)
        except RupeeBudgetExceededError as e:
            status = ExecutionStatus.BUDGET_EXCEEDED
            reason = str(e)
        except TimeoutBudgetExceededError as e:
            status = ExecutionStatus.BUDGET_EXCEEDED
            reason = str(e)
        except Exception as e:
            logger.exception("Unexpected execution error in runner")
            status = ExecutionStatus.FAILED
            reason = f"Execution error: {e}"

        # Generate summary
        summary = (
            f"Completed task in {self.budget.steps_taken} steps with status {status.value}. "
            f"Total cost: ₹{self.budget.current_cost_inr:.4f}. Artifacts generated: {len(self.artifacts)}."
        )

        report = RunReport(
            task=self.task,
            status=status,
            reason=reason,
            steps_taken=self.budget.steps_taken,
            max_steps=self.budget.max_steps,
            total_cost_inr=self.budget.current_cost_inr,
            max_cost_inr=self.budget.max_cost_inr,
            cost_breakdown=self.budget.cost_breakdown,
            duration_seconds=self.budget.elapsed_seconds,
            input_files=self.files,
            artifacts=self.artifacts,
            verification_results=self.verification_results,
            plan=plan,
            final_summary=summary,
        )

        self.reporter.write_report_json(report)
        self.reporter.write_result_md(report, self.traces)
        return report
