"""
Task Agent execution loop for Bodhan AI.
Orchestrates input inspection, LLM planning, tool calls, verification, and output writing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import re
import shutil
import sys
import time
from typing import Any

from bodhan_agent.budget import Budget
from bodhan_agent.planner import Planner
from bodhan_agent.report import Reporter
from bodhan_agent.trace import Tracer
from bodhan_agent.verifier import Verifier
from bodhan_core.cache import ToolCache
from bodhan_core.client import BodhanClient
from bodhan_core.cost import CostMeter
from bodhan_core.workspace import Workspace
from bodhan_mcp.server import create_server

log = logging.getLogger(__name__)


def _substitute_refs(args: dict[str, Any], step_results: dict[str, Any], files_dir: pathlib.Path) -> dict[str, Any]:
    """Substitute references like {step_0_output}, {file_content}, or <transcribed_text> in tool arguments."""
    resolved = dict(args)

    # Helper to find latest text from prior steps
    latest_text = ""
    latest_file = ""
    for _, res in sorted(step_results.items()):
        data = res.get("data", {})
        t = data.get("translated_text") or data.get("text") or data.get("markdown")
        if t:
            if isinstance(t, str) and t.startswith("{") and '"text":' in t:
                try:
                    nested = json.loads(t)
                    if isinstance(nested, dict) and "text" in nested:
                        t = nested["text"]
                except Exception:
                    pass
            latest_text = t
        f = data.get("output_file")
        if f:
            latest_file = f

    for k, v in list(resolved.items()):
        if isinstance(v, str):
            # Check for {step_N_output}
            m = re.match(r"^\{step_(\d+)_output\}$", v.strip())
            if m:
                step_idx = f"step_{m.group(1)}"
                prev_data = step_results.get(step_idx, {}).get("data", {})
                resolved[k] = (
                    prev_data.get("output_file")
                    or prev_data.get("translated_text")
                    or prev_data.get("text")
                    or prev_data.get("markdown")
                    or ""
                )
                continue

            # Check for LLM placeholder tokens like <transcribed_text>, <text>, <transcript>
            v_clean = v.strip().lower()
            if v_clean in {"<transcribed_text>", "<transcript>", "<text>", "<translation>", "<translated_text>"} or (v_clean.startswith("<") and v_clean.endswith(">")):
                if latest_text:
                    resolved[k] = latest_text
                    continue

            # Check for {file_content}
            if "{file_content}" in v:
                file_arg = resolved.get("file_path")
                if file_arg:
                    p = pathlib.Path(file_arg)
                    if not p.is_absolute():
                        p = files_dir / p
                    if p.exists() and p.is_file():
                        content = p.read_text(encoding="utf-8", errors="replace")
                        resolved[k] = v.replace("{file_content}", content)

    return resolved


async def run_task(
    task: str,
    files: pathlib.Path,
    out_dir: pathlib.Path,
    max_steps: int = 30,
    max_cost_inr: float = 20.0,
) -> None:
    """Execute a plain-language task over input files and produce required outputs."""
    files = pathlib.Path(files).resolve()
    out_dir = pathlib.Path(out_dir).resolve()
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    tracer = Tracer(out_dir)
    tracer.open()
    reporter = Reporter(out_dir, task)
    budget = Budget(max_steps=max_steps, max_cost_inr=max_cost_inr)
    planner = Planner()

    # Shared infrastructure
    cache_dir = out_dir / ".cache"
    cache = ToolCache(cache_dir)
    cost_meter = CostMeter()
    client = BodhanClient(cache=cache, cost_meter=cost_meter, max_budget_inr=max_cost_inr)
    verifier = Verifier(client=client)

    workspace_root = files if files.is_dir() else files.parent
    os.environ["BODHAN_WORKSPACE"] = str(workspace_root)
    os.environ["BODHAN_CACHE_DIR"] = str(cache_dir)

    server = create_server()

    # Helper to call tool through FastMCP
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # FastMCP call_tool returns a list of Content objects or dict
        result = await server.call_tool(name, arguments)
        if isinstance(result, list) and len(result) > 0:
            content_item = result[0]
            text_val = getattr(content_item, "text", None) or str(content_item)
            try:
                return json.loads(text_val)
            except Exception:
                return {"ok": True, "data": {"raw": text_val}}
        if isinstance(result, dict):
            return result
        return {"ok": True, "data": result}

    try:
        # Phase 1: File discovery and inspection (free)
        input_paths: list[pathlib.Path] = []
        if files.is_dir():
            input_paths = [p for p in files.iterdir() if p.is_file() and not p.name.startswith(".")]
        elif files.is_file():
            input_paths = [files]

        file_metadata: list[dict[str, Any]] = []
        for p in input_paths:
            t0 = time.monotonic()
            insp = await call_tool("inspect_media", {"file_path": str(p)})
            dur_ms = int((time.monotonic() - t0) * 1000)
            tracer.log(
                "inspect_media",
                file=str(p),
                duration_ms=dur_ms,
                result=insp,
            )
            file_metadata.append({
                "path": str(p),
                "name": p.name,
                "metadata": insp.get("data", {}),
            })

        # Phase 2: Planning
        plan = await planner.plan(task, file_metadata)
        tracer.log("plan_created", plan=plan)

        for a in plan.get("assumptions", []):
            reporter.add_assumption(a)

        if not plan.get("feasible", True):
            reason = plan.get("refusal_reason", "Task is not feasible with available Bodhan models.")
            tracer.log("task_refused", reason=reason)
            reporter.set_status("refused")
            reporter.set_budget(budget.snapshot())
            reporter.write(summary=reason)
            print(f"Task Refused: {reason}", file=sys.stderr)
            return

        steps = plan.get("steps", [])
        step_results: dict[str, Any] = {}

        # Phase 3: Step Execution
        for idx, step in enumerate(steps):
            if not budget.ok():
                reason = budget.reason_stopped()
                tracer.log("budget_limit_reached", reason=reason)
                reporter.set_status("partial")
                break

            budget.tick()
            tracer.log("step_started", step_index=idx, step=step)

            action = step.get("action", "tool_call")
            tool_name = step.get("tool", "")
            raw_args = step.get("args", {})
            resolved_args = _substitute_refs(raw_args, step_results, files if files.is_dir() else files.parent)

            # Pre-flight check
            t0 = time.monotonic()
            result: dict[str, Any] = {}
            error_msg: str | None = None

            for attempt in range(2):
                try:
                    result = await call_tool(tool_name, resolved_args)
                    if not result.get("ok", True):
                        error_msg = result.get("error", {}).get("message", "Tool returned failure")
                    else:
                        error_msg = None
                        break
                except Exception as exc:
                    error_msg = str(exc)
                    await asyncio.sleep(1.0)

            dur_ms = int((time.monotonic() - t0) * 1000)
            cost = result.get("cost_inr", 0.0)
            budget.add_cost(cost)

            step_key = f"step_{idx}"
            step_results[step_key] = result

            tracer.log(
                "step_completed",
                step_index=idx,
                tool=tool_name,
                args=resolved_args,
                cost_inr=cost,
                duration_ms=dur_ms,
                cached=result.get("cached", False),
                ok=(error_msg is None),
                error=error_msg,
            )

            reporter.add_step({
                "step_index": idx,
                "tool": tool_name,
                "status": "ok" if not error_msg else "failed",
                "cost_inr": cost,
                "duration_ms": dur_ms,
                "error": error_msg,
            })

            # Check if an artifact was generated
            data = result.get("data", {})
            out_file = data.get("output_file")
            if out_file:
                out_p = pathlib.Path(out_file)
                if out_p.exists():
                    dest_artifact = artifacts_dir / out_p.name
                    try:
                        shutil.copyfile(out_p, dest_artifact)
                        reporter.add_artifact(str(dest_artifact))
                    except Exception:
                        reporter.add_artifact(str(out_p))

            # Run verifications where applicable
            if tool_name == "synthesize_speech" and out_file:
                v_res = await verifier.verify_audio(
                    pathlib.Path(out_file),
                    source_text=resolved_args.get("text", ""),
                    language=resolved_args.get("language"),
                )
                reporter.add_verification(v_res)
                tracer.log("verification_audio", result=v_res)

            elif tool_name == "translate_text":
                tgt_text = data.get("translated_text", "")
                src_text = resolved_args.get("text", "")
                v_res = verifier.verify_translation(
                    source_text=src_text,
                    translated_text=tgt_text,
                    target_lang=resolved_args.get("target_lang", "en"),
                )
                reporter.add_verification(v_res)
                tracer.log("verification_translation", result=v_res)

        # Phase 4: Summarization & Reporting
        reporter.set_budget(budget.snapshot())
        all_passed = all(s.get("status") == "ok" for s in reporter.steps) and len(reporter.steps) > 0
        final_status = "completed" if all_passed else ("partial" if reporter.steps else "failed")
        reporter.set_status(final_status)

        summary = await planner.summarize(task, reporter.steps, reporter.artifacts)
        reporter.write(summary=summary)

    except Exception as exc:
        log.error("Unhandled fatal exception in agent loop: %s", exc, exc_info=True)
        tracer.log("fatal_exception", error=str(exc))
        reporter.set_status("failed")
        reporter.set_budget(budget.snapshot())
        reporter.write(summary=f"Task execution failed: {exc}")
        raise
    finally:
        tracer.close()
