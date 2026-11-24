"""Main orchestration loop: plan, act, verify, replan, stop."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional

from bodhan_agent import executor, planner, report, verifier
from bodhan_agent.budget import BudgetExceeded, BudgetTracker
from bodhan_agent.planner import get_tool_list
from bodhan_agent.state import (
    FileInfo,
    PlanStep,
    StepStatus,
    TaskState,
    TaskStatus,
)
from bodhan_agent.trace import TraceWriter
from bodhan_mcp.tools.media import inspect_media

logger = logging.getLogger(__name__)


def inspect_input_files(files_path: str | Path) -> list[FileInfo]:
    """
    Inspect input files deterministically using Python and media helpers.
    Extract file sizes, mime types, durations, page counts, detected scripts.
    """
    path_obj = Path(files_path)
    if not path_obj.exists():
        logger.warning("Input path does not exist: %s", files_path)
        return []

    target_files: list[Path] = []
    if path_obj.is_dir():
        for p in path_obj.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                target_files.append(p)
    elif path_obj.is_file():
        target_files.append(path_obj)

    file_infos: list[FileInfo] = []
    for f in target_files:
        size = f.stat().st_size
        suffix = f.suffix.lower()

        # Classify basic type
        file_type = "unknown"
        if suffix in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac"):
            file_type = "audio"
        elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
            file_type = "image"
        elif suffix == ".pdf":
            file_type = "pdf"
        elif suffix in (".txt", ".md", ".json", ".csv"):
            file_type = "text"
        elif suffix in (".mp4", ".mkv", ".mov", ".avi"):
            file_type = "video"

        # Inspect deeper metadata with inspect_media
        duration = None
        pages = None
        script = None
        mime = None

        try:
            insp = inspect_media(str(f.resolve()))
            mime = insp.mime_type
            duration = insp.duration_seconds
            pages = insp.num_pages
            script = insp.detected_script
        except Exception as exc:
            logger.debug("inspect_media failed for %s: %s", f.name, exc)
            mime, _ = mimetypes.guess_type(str(f))

        file_infos.append(
            FileInfo(
                path=str(f.resolve()),
                name=f.name,
                type=file_type,
                size_bytes=size,
                duration_seconds=duration,
                page_count=pages,
                detected_script=script,
                mime_type=mime,
            )
        )

    logger.info("Inspected %d input files from %s", len(file_infos), files_path)
    return file_infos


def _dep_done(dep_id: str, state: TaskState) -> bool:
    """Check if a dependency step is complete."""
    step = state.get_step(dep_id)
    return step is not None and step.status == StepStatus.DONE


def _all_steps_done(state: TaskState) -> bool:
    """Check if all planned steps are complete."""
    if not state.plan:
        return False
    return all(s.status == StepStatus.DONE for s in state.plan)


async def run(
    task: str,
    files_dir: Optional[str] = None,
    text: Optional[str] = None,
    out_dir: str = "./out",
    max_steps: int = 30,
    max_cost_inr: float = 20.0,
    max_time_s: float = 300.0,
) -> TaskState:
    """
    Run the Bodhan Task Agent:
    1. Inspect input files or direct text input
    2. Interpret task (feasibility check)
    3. Make execution plan
    4. Execute plan (sequential or parallel)
    5. Verify outputs
    6. Write result.md, report.json, and trace.jsonl
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "artifacts").mkdir(exist_ok=True)

    state = TaskState(task=task, out_dir=str(out_path), input_text=text)
    budget = BudgetTracker(max_steps=max_steps, max_cost_inr=max_cost_inr, max_time_s=max_time_s)
    trace = TraceWriter(out_path)

    logger.info("Starting Bodhan Agent task: '%s'", task)

    # ── 1. Deterministic file inspection & direct text input ──────────────────
    if files_dir:
        state.files = inspect_input_files(files_dir)
    else:
        state.files = []

    if text:
        input_text_file = out_path / "artifacts" / "input_text.txt"
        input_text_file.write_text(text, encoding="utf-8")
        text_info = FileInfo(
            path=str(input_text_file.resolve()),
            name="input_text.txt",
            type="text",
            size_bytes=len(text.encode("utf-8")),
            duration_seconds=None,
            page_count=None,
            detected_script=None,
            mime_type="text/plain",
        )
        state.files.append(text_info)

    if not state.files:
        state.status = TaskStatus.IMPOSSIBLE
        state.stop_reason = f"No input files found in '{files_dir}' or direct text to process." if files_dir else "No input files or direct text provided to process."
        logger.warning("Impossible task: %s", state.stop_reason)
        report.write_result_md(state, out_path)
        report.write_report_json(state, out_path)
        trace.close()
        return state

    # ── 2. Interpret Task (LLM) ───────────────────────────────────────────────
    tools = get_tool_list()
    interpretation = planner.interpret_task(task, state.files, tools, budget, input_text=text)
    state.goal = interpretation.goal
    state.deliverables = interpretation.deliverables
    state.assumptions = interpretation.assumptions

    trace.record_decision(
        "interpret_task",
        interpretation.model_dump(),
        rationale=interpretation.reason,
    )

    if not interpretation.feasible:
        state.status = TaskStatus.IMPOSSIBLE
        state.stop_reason = interpretation.reason
        logger.warning("Task marked IMPOSSIBLE: %s", state.stop_reason)
        report.write_result_md(state, out_path)
        report.write_report_json(state, out_path)
        trace.close()
        return state

    # ── 3. Generate Plan (LLM) ────────────────────────────────────────────────
    state.plan = planner.make_plan(interpretation, state.files, tools, input_text=text)
    trace.record_plan(state.plan, rationale="Initial execution DAG")
    logger.info("Generated initial plan with %d steps", len(state.plan))

    # ── 4. Main Execution Loop ────────────────────────────────────────────────
    while True:
        # Check budget limits
        try:
            budget.check()
        except BudgetExceeded as exc:
            state.status = TaskStatus.BUDGET_EXHAUSTED
            state.stop_reason = str(exc)
            logger.warning("Budget limit reached: %s", exc)
            break

        # Find steps ready to execute (all dependencies completed)
        ready = [
            s for s in state.plan
            if s.status == StepStatus.PENDING and all(_dep_done(d, state) for d in s.depends_on)
        ]

        if not ready:
            # No steps ready. Check if all steps done
            if _all_steps_done(state):
                logger.info("All plan steps completed successfully.")
                break

            # Check if any step failed and requires replanning
            failed_steps = [s for s in state.plan if s.status == StepStatus.FAILED]
            if failed_steps and state.replan_count < 2:
                state.replan_count += 1
                logger.info("Attempting replan (%d/2) after step failure...", state.replan_count)
                new_steps = planner.replan(state, failed_steps[0], state.goal, tools)
                if new_steps:
                    state.plan.extend(new_steps)
                    trace.record_plan(new_steps, rationale=f"Replan attempt {state.replan_count}")
                    continue

            # Cannot proceed further
            unresolved = [s for s in state.plan if s.status == StepStatus.PENDING]
            if unresolved or failed_steps:
                state.status = TaskStatus.PARTIAL if state.artifacts else TaskStatus.FAILED
                state.stop_reason = f"Plan stalled or failed on step(s): {[s.id for s in failed_steps]}"
            break

        # Execute ready steps: single or parallel
        if len(ready) == 1:
            await executor.execute_step(ready[0], state, budget, trace)
        else:
            await executor.execute_parallel(ready, state, budget, trace)

        state.elapsed_s = time.time() - state.start_time

    # ── 5. Verify Outputs ─────────────────────────────────────────────────────
    logger.info("Running output verifications on %d artifacts...", len(state.artifacts))
    try:
        verifications = await verifier.verify_all(state, out_path)
        state.verifications = verifications
        trace.record_verification(verifications)
    except Exception as exc:
        logger.warning("Verification stage encountered error: %s", exc)

    # ── 6. Finalize Status ────────────────────────────────────────────────────
    state.elapsed_s = time.time() - state.start_time
    state.spent_inr = budget.spent_inr
    state.steps_used = budget.steps_used

    if state.status == TaskStatus.RUNNING:
        if state.artifacts or _all_steps_done(state):
            if state.stop_reason or any("not supported" in a.lower() or "unsupported" in a.lower() for a in state.assumptions):
                state.status = TaskStatus.PARTIAL
            else:
                state.status = TaskStatus.COMPLETED
        else:
            state.status = TaskStatus.FAILED

    logger.info(
        "Task finished: status=%s, spent=₹%.4f, steps=%d, time=%.2fs",
        state.status,
        state.spent_inr,
        state.steps_used,
        state.elapsed_s,
    )

    # ── 7. Write Result & Report ──────────────────────────────────────────────
    report.write_result_md(state, out_path)
    report.write_report_json(state, out_path)
    trace.close()

    return state
