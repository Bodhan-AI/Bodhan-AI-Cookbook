"""Execution engine: dependency DAG resolution, concurrent execution, budgeting, and caching."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from bodhan_agent.budget import BudgetManager
from bodhan_agent.cache import ResultCache
from bodhan_agent.checkpoint import CheckpointManager
from bodhan_agent.llm import LLMClient
from bodhan_agent.models import ExecutionPlan, PlanStep, StepStatus, TraceEvent
from bodhan_agent.trace import TraceLogger
from bodhan_mcp.errors import BodhanError
from bodhan_mcp.tools.ocr import extract_text_from_document
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text

logger = logging.getLogger("bodhan_agent.executor")


class PlanExecutor:
    """Executes an ExecutionPlan with dependency checking, caching, and budget tracking."""

    def __init__(
        self,
        budget: BudgetManager,
        cache: ResultCache,
        trace_logger: TraceLogger,
        checkpoint: CheckpointManager,
        llm: LLMClient | None = None,
        max_parallel_workers: int = 4,
    ):
        self.budget = budget
        self.cache = cache
        self.trace_logger = trace_logger
        self.checkpoint = checkpoint
        self.llm = llm or LLMClient()
        self.max_parallel_workers = max_parallel_workers

    def _resolve_step_arguments(self, step: PlanStep, step_results: dict[str, Any]) -> dict[str, Any]:
        """Resolve inputs that depend on prior step outputs."""
        args = dict(step.tool_arguments)
        source_id = args.get("source_step_id")
        if source_id and source_id in step_results:
            source_res = step_results[source_id]
            if isinstance(source_res, dict):
                if "translation" in source_res and source_res["translation"]:
                    args["text"] = source_res["translation"]
                elif "text" in source_res and source_res["text"]:
                    args["text"] = source_res["text"]
        if "text" not in args:
            args["text"] = ""
        return args

    def _run_single_step(self, step: PlanStep, resolved_args: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a single tool operation with caching and error handling."""
        tool = step.tool_name

        # Check result cache
        cached = self.cache.get(tool, resolved_args)
        if cached:
            return cached

        # Execute according to tool name
        if tool == "transcribe_audio":
            out = transcribe_audio(
                file_path=resolved_args["file_path"],
                language=resolved_args.get("language"),
            )
            res = out.model_dump() if hasattr(out, "model_dump") else dict(out)

        elif tool == "translate_text":
            out = translate_text(
                text=resolved_args["text"],
                target_language=resolved_args["target_language"],
                script=resolved_args.get("script", "native"),
            )
            res = out.model_dump() if hasattr(out, "model_dump") else dict(out)

        elif tool == "synthesize_speech":
            out = synthesize_speech(
                text=resolved_args["text"],
                language=resolved_args["language"],
                voice=resolved_args.get("voice"),
                gender_preference=resolved_args.get("gender_preference", "female"),
                style=resolved_args.get("style"),
                output_file_path=resolved_args.get("output_file_path"),
            )
            res = out.model_dump() if hasattr(out, "model_dump") else dict(out)

        elif tool == "extract_text_from_document":
            out = extract_text_from_document(
                file_path=resolved_args["file_path"],
                table_format=resolved_args.get("table_format", "markdown"),
            )
            res = out.model_dump() if hasattr(out, "model_dump") else dict(out)

        elif tool == "llm_summarize":
            summary_text = self.llm.summarize_in_english(
                context_text=resolved_args.get("text", ""),
                focus=resolved_args.get("focus", "general summary"),
            )
            res = {"success": True, "text": summary_text, "estimated_cost_inr": 0.0}

        elif tool == "format_subtitles":
            # Format chunks into SRT
            source_res = resolved_args.get("source_res", {})
            chunks = source_res.get("chunks", [])
            srt_lines = []
            if chunks:
                for c in chunks:
                    idx = c["chunk_index"] + 1
                    s_sec = int(c["start_time_s"])
                    e_sec = int(c["end_time_s"])
                    srt_lines.append(
                        f"{idx}\n00:00:{s_sec:02d},000 --> 00:00:{e_sec:02d},000\n{c['text']}\n"
                    )
            else:
                full_text = resolved_args.get("text", "")
                srt_lines.append(f"1\n00:00:00,000 --> 00:00:10,000\n{full_text}\n")
            res = {"success": True, "srt_content": "\n".join(srt_lines), "estimated_cost_inr": 0.0}

        else:
            raise BodhanError(f"Unknown tool: {tool}")

        # Store in cache
        self.cache.set(tool, resolved_args, res)
        return res

    def execute_plan(
        self,
        task_id: str,
        user_prompt: str,
        plan: ExecutionPlan,
    ) -> dict[str, Any]:
        """Execute all steps in the plan respecting dependencies."""
        step_results: dict[str, Any] = {}
        completed_step_ids: set[str] = set()

        for idx, step in enumerate(plan.steps):
            # Check dependencies
            unmet = [dep for dep in step.dependencies if dep not in completed_step_ids]
            if unmet:
                step.status = StepStatus.FAILED
                step.error_message = f"Unmet dependencies: {', '.join(unmet)}"
                continue

            # Check budget limits before execution
            self.budget.check_can_execute_step(step.estimated_cost_inr)

            step.status = StepStatus.RUNNING
            t_start = time.time()

            # Record step start event
            self.trace_logger.record_event(
                TraceEvent(
                    task_id=task_id,
                    step_id=step.step_id,
                    event_type="step_start",
                    tool_name=step.tool_name,
                    status="running",
                )
            )

            resolved_args = self._resolve_step_arguments(step, step_results)
            if step.tool_name == "format_subtitles" and step.dependencies:
                resolved_args["source_res"] = step_results.get(step.dependencies[0], {})

            try:
                res = self._run_single_step(step, resolved_args)
                latency = round((time.time() - t_start) * 1000.0, 2)
                actual_cost = float(res.get("estimated_cost_inr", 0.0))

                step.status = StepStatus.COMPLETED
                step.result_data = res
                step.duration_ms = latency
                step_results[step.step_id] = res
                completed_step_ids.add(step.step_id)

                # Save output artifact if applicable
                if step.expected_output_artifact:
                    art_path = Path(step.expected_output_artifact)
                    art_path.parent.mkdir(parents=True, exist_ok=True)
                    if "text" in res:
                        art_path.write_text(res["text"], encoding="utf-8")
                    elif "translation" in res:
                        art_path.write_text(res["translation"], encoding="utf-8")
                    elif "srt_content" in res:
                        art_path.write_text(res["srt_content"], encoding="utf-8")

                # Track budget
                self.budget.record_step_execution(actual_cost)

                # Record step completion event
                self.trace_logger.record_event(
                    TraceEvent(
                        task_id=task_id,
                        step_id=step.step_id,
                        event_type="step_complete",
                        tool_name=step.tool_name,
                        sanitized_arguments=resolved_args,
                        result_summary=f"Success: {step.description}",
                        status="completed",
                        latency_ms=latency,
                        actual_cost_inr=actual_cost,
                    )
                )

                # Persist checkpoint
                self.checkpoint.save_checkpoint(
                    task_id=task_id,
                    user_prompt=user_prompt,
                    plan=plan,
                    spent_cost_inr=self.budget.current_cost_inr,
                    step_index=idx,
                )

            except Exception as e:
                latency = round((time.time() - t_start) * 1000.0, 2)
                step.status = StepStatus.FAILED
                step.error_message = str(e)
                logger.error(f"Step {step.step_id} ({step.tool_name}) failed: {e}")

                self.trace_logger.record_event(
                    TraceEvent(
                        task_id=task_id,
                        step_id=step.step_id,
                        event_type="error",
                        tool_name=step.tool_name,
                        sanitized_arguments=resolved_args,
                        result_summary=f"Failed: {e}",
                        status="failed",
                        latency_ms=latency,
                    )
                )
                # Halt remaining dependent steps
                break

        return step_results
