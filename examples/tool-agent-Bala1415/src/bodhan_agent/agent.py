"""
Agent — the main Plan→Act→Verify→Retry loop.

This is the core orchestration engine that:
1. Inspects input files
2. Sends the task to the LLM planner
3. Executes tool calls from the planner
4. Feeds results back to the planner
5. Verifies outputs
6. Writes final results

The loop continues until the planner calls task_complete,
the budget is exhausted, or max retries are reached.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from bodhan_mcp.client import BodhanClient, CostTracker, ResponseCache
from bodhan_mcp.media import inspect_file
from bodhan_agent.planner import Planner
from bodhan_agent.executor import Executor
from bodhan_agent.verifier import (
    verify_audio_output,
    verify_translation_coverage,
    verify_file_exists,
)
from bodhan_agent.budget import BudgetController, BudgetExhausted
from bodhan_agent.tracer import Tracer
from bodhan_agent.output import write_result_md, setup_output_dir, save_artifact

logger = logging.getLogger(__name__)

MAX_AGENT_ITERATIONS = 50  # Hard safety limit on LLM loops


class Agent:
    """
    The task agent that plans, executes, verifies, and reports.

    Usage:
        agent = Agent(task="...", files_dir="...", output_dir="./out")
        result = await agent.run()
    """

    def __init__(
        self,
        task: str,
        files_dir: str,
        output_dir: str = "./out",
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        timeout_seconds: float = 300.0,
    ):
        self.task = task
        self.files_dir = Path(files_dir)
        self.output_dir = setup_output_dir(output_dir)

        # Budget
        self.budget = BudgetController(
            max_steps=max_steps,
            max_cost_inr=max_cost_inr,
            timeout_seconds=timeout_seconds,
        )

        # API client with cost tracking
        self.cost_tracker = CostTracker(max_cost_inr=max_cost_inr)
        self.cache = ResponseCache()
        self.client = BodhanClient(
            cost_tracker=self.cost_tracker,
            cache=self.cache,
        )

        # Planner, executor, tracer
        self.planner = Planner()
        self.executor = Executor(client=self.client, output_dir=self.output_dir)
        self.tracer = Tracer(output_dir=self.output_dir)

        # State
        self._artifacts: list[dict[str, Any]] = []
        self._assumptions: list[str] = []
        self._errors: list[str] = []
        self._completed = False
        self._final_status = "unknown"
        self._final_summary = ""

    async def run(self) -> dict[str, Any]:
        """
        Execute the full agent loop.

        Returns:
            dict with 'status', 'summary', 'artifacts', 'report'.
        """
        logger.info(f"Starting task: {self.task}")
        logger.info(f"Files dir: {self.files_dir}")
        logger.info(f"Output dir: {self.output_dir}")

        try:
            # Step 1: Inspect input files
            file_descriptions = self._inspect_input_files()
            self.tracer.record(
                type="init",
                result_summary=f"Found {len(file_descriptions)} input file(s)",
                files=file_descriptions,
            )

            # Step 2: Send to planner for initial plan
            budget_info = self.budget.remaining()
            response = await self.planner.plan(
                task=self.task,
                file_descriptions=file_descriptions,
                budget_info=budget_info,
            )

            # Step 3: Main loop — execute tool calls, feed results back
            iteration = 0
            while iteration < MAX_AGENT_ITERATIONS and not self._completed:
                iteration += 1

                try:
                    self.budget.check()
                except BudgetExhausted as e:
                    logger.warning(f"Budget exhausted: {e}")
                    self._errors.append(str(e))
                    self._final_status = "partial"
                    self._final_summary = f"Task stopped due to budget: {e}"
                    self.tracer.record_error(str(e), recoverable=False)
                    break

                tool_calls = response.get("tool_calls", [])

                if not tool_calls:
                    # LLM responded with text only — might be done or confused
                    content = response.get("content", "")
                    if content:
                        self.tracer.record(type="llm_response", result_summary=content[:300])
                    # If no tool calls and finish_reason is "stop", we're done
                    if response.get("finish_reason") == "stop":
                        if not self._completed:
                            self._final_status = "completed"
                            self._final_summary = content or "Task completed."
                            self._completed = True
                    break

                # Execute each tool call
                for tc in tool_calls:
                    tool_name = tc["name"]
                    arguments = tc["arguments"]
                    tool_call_id = tc["id"]

                    logger.info(f"Executing: {tool_name}({json.dumps(arguments)[:200]})")

                    # Check for task_complete signal
                    if tool_name == "task_complete":
                        self._completed = True
                        self._final_status = arguments.get("status", "completed")
                        self._final_summary = arguments.get("summary", "")
                        self._assumptions.extend(arguments.get("assumptions", []))
                        for art in arguments.get("artifacts", []):
                            self._artifacts.append(art)
                        self.tracer.record(
                            type="task_complete",
                            result_summary=self._final_summary,
                        )
                        # Still need to respond to the LLM
                        response = await self.planner.continue_with_result(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            result=json.dumps({"status": "acknowledged"}),
                        )
                        break

                    # Execute the tool
                    start_time = time.time()
                    result = await self.executor.execute(tool_name, arguments)
                    latency = time.time() - start_time

                    # Extract cost from result or cost tracker
                    cost = result.pop("_latency_seconds", 0)
                    step_cost = result.get("cost_inr", 0)

                    # Record in budget and tracer
                    self.budget.record_step(
                        tool=tool_name,
                        cost_inr=step_cost,
                        latency_seconds=latency,
                        details=_summarize_result(result),
                    )
                    self.tracer.record_tool_call(
                        tool=tool_name,
                        arguments=arguments,
                        result_summary=_summarize_result(result),
                        latency_seconds=latency,
                        cost_inr=step_cost,
                    )

                    # Track artifacts from tool results
                    if "output_path" in result:
                        self._artifacts.append({
                            "name": Path(result["output_path"]).name,
                            "path": result["output_path"],
                            "description": f"Generated by {tool_name}",
                        })

                    # Feed result back to planner
                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                    # Truncate very long results to save LLM context
                    if len(result_str) > 8000:
                        truncated = result.copy()
                        for key in ["text", "translated_text", "blocks", "segments"]:
                            if key in truncated and isinstance(truncated[key], str) and len(truncated[key]) > 2000:
                                truncated[key] = truncated[key][:2000] + f"... [truncated, {len(result[key])} chars total]"
                            elif key in truncated and isinstance(truncated[key], list) and len(truncated[key]) > 5:
                                truncated[key] = truncated[key][:5] + [{"note": f"... {len(result[key])} items total"}]
                        result_str = json.dumps(truncated, ensure_ascii=False, default=str)

                    response = await self.planner.continue_with_result(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        result=result_str,
                        budget_info=self.budget.remaining(),
                    )

                    if self._completed:
                        break

        except BudgetExhausted as e:
            self._final_status = "partial"
            self._final_summary = f"Budget exhausted: {e}"
            self._errors.append(str(e))
            self.tracer.record_error(str(e), recoverable=False)
        except Exception as e:
            logger.exception("Agent failed with unexpected error")
            self._final_status = "failed"
            self._final_summary = f"Agent error: {str(e)}"
            self._errors.append(str(e))
            self.tracer.record_error(str(e), recoverable=False)
        finally:
            await self.client.close()

        # Write final outputs
        return self._finalize()

    def _inspect_input_files(self) -> list[dict[str, Any]]:
        """Inspect all input files and return metadata."""
        descriptions = []

        if self.files_dir.is_file():
            # Single file passed
            info = inspect_file(str(self.files_dir))
            descriptions.append(info)
        elif self.files_dir.is_dir():
            # Directory of files
            for f in sorted(self.files_dir.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    info = inspect_file(str(f))
                    descriptions.append(info)
        else:
            self._errors.append(f"Input path not found: {self.files_dir}")

        return descriptions

    def _finalize(self) -> dict[str, Any]:
        """Write result.md, report.json, and return the final summary."""
        # Sync cost from API client to budget
        self.budget.total_cost_inr = max(
            self.budget.total_cost_inr,
            self.cost_tracker.total_cost_inr,
        )

        # Write result.md
        write_result_md(
            output_dir=self.output_dir,
            task=self.task,
            status=self._final_status,
            summary=self._final_summary,
            artifacts=self._artifacts,
            assumptions=self._assumptions,
            errors=self._errors,
        )

        # Write report.json
        report = self.tracer.generate_report(
            status=self._final_status,
            task=self.task,
            budget_summary=self.budget.summary(),
            assumptions=self._assumptions,
        )

        logger.info(
            f"Task complete: status={self._final_status}, "
            f"steps={self.budget.current_step}, "
            f"cost=₹{self.budget.total_cost_inr:.2f}"
        )

        return {
            "status": self._final_status,
            "summary": self._final_summary,
            "artifacts": self._artifacts,
            "report": report,
        }


def _summarize_result(result: dict[str, Any]) -> str:
    """Create a short text summary of a tool result for logging."""
    if result.get("error"):
        return f"ERROR: {result.get('message', 'unknown error')}"

    parts = []
    if "text" in result:
        text = result["text"]
        parts.append(f"text={len(text)} chars")
    if "translated_text" in result:
        parts.append(f"translated={len(result['translated_text'])} chars")
    if "output_path" in result:
        parts.append(f"file={Path(result['output_path']).name}")
    if "language_code" in result:
        parts.append(f"lang={result['language_code']}")
    if "category" in result:
        parts.append(f"type={result['category']}")
    if "num_pages" in result:
        parts.append(f"pages={result['num_pages']}")
    if "duration_seconds" in result:
        parts.append(f"dur={result['duration_seconds']}s")
    if "num_chunks" in result:
        parts.append(f"chunks={result['num_chunks']}")

    return ", ".join(parts) if parts else "OK"
