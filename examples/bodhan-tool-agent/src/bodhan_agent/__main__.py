from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from bodhan_mcp.cache import CostLedger, DiskCache
from bodhan_mcp.client import BodhanClient, ClientConfig
from bodhan_mcp.models import ErrorCode, ToolError
from bodhan_mcp.workspace import Workspace

from .loop import AgentExecutionResult, BodhanAgentLoop
from .planner import AgentPlanner, TaskPlan
from .preflight import inspect_task_inputs, validate_plan

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("bodhan_agent")

_DENIED_DIRS = {".git", ".ssh", ".aws", ".gnupg"}
_DENIED_NAMES = {"credentials", "id_rsa", "id_ed25519"}
_DENIED_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
_MAX_FOLLOWUP_ROUNDS = 3


def _safe_input(path: Path) -> bool:
    return not (
        path.is_symlink()
        or any(part.lower() in _DENIED_DIRS for part in path.parts)
        or path.name.lower().startswith(".env")
        or path.name.lower() in _DENIED_NAMES
        or path.suffix.lower() in _DENIED_SUFFIXES
    )


def _derive_status(result: AgentExecutionResult) -> str:
    if result.success:
        return "completed"
    reason = (result.stopped_reason or "").lower()
    if "refused" in reason:
        return "refused"
    if "budget" in reason or "maximum step" in reason or "cost" in reason:
        return "budget_exceeded"
    return "failed"


def _render_result_md(
    out_dir: Path,
    task: str,
    result: AgentExecutionResult,
    elapsed_seconds: float,
    workspace: Workspace | None,
) -> str:
    status = _derive_status(result)
    lines = [
        "# Task Execution Result",
        "",
        f"- **Task:** {task}",
        f"- **Status:** `{status}`",
        f"- **Success:** {result.success}",
        f"- **Outcome:** {result.stopped_reason or 'completed'}",
        f"- **Total Bodhan cost:** ₹{result.total_cost_inr:.6f}",
        f"- **Steps executed:** {len(result.steps)}",
        f"- **Elapsed time:** {elapsed_seconds:.2f}s",
        "",
        "## What Was Done",
        "",
    ]

    if result.steps:
        for step in result.steps:
            summary = step.get("result_summary", {})
            ok_str = "ok" if summary.get("ok") else "failed"
            cached_tag = " (cached)" if step.get("cached") else ""
            lines.append(
                f"- Step `{step.get('step_id')}` (`{step.get('tool')}`): "
                f"{ok_str}, ₹{float(step.get('cost_inr', 0.0)):.6f}{cached_tag}, "
                f"{int(step.get('latency_ms', 0))}ms"
            )
    else:
        lines.append("- No model tools were executed.")

    lines.extend(["", "## Final Answer", ""])
    public_output = {
        k: v for k, v in (result.final_output or {}).items() if not k.startswith("_")
    } if isinstance(result.final_output, dict) else result.final_output

    if not result.success and isinstance(public_output, dict) and "error" in public_output:
        err = public_output["error"]
        lines.append(f"**Error (`{err.get('code')}`):** {err.get('message')}")
        if err.get("hint"):
            lines.append(f"\n**Next step:** {err.get('hint')}")
    elif isinstance(public_output, dict) and public_output.get("deliverables"):
        for name, val in public_output["deliverables"].items():
            if isinstance(val, str):
                text_val = val
            else:
                text_val = json.dumps(val, ensure_ascii=False, indent=2)
            if len(text_val) <= 4000:
                excerpt = text_val
            else:
                excerpt = text_val[:4000] + "\n... (truncated; see artifact)"

            lines.extend([f"### {name}", "", excerpt, ""])
    elif isinstance(public_output, dict):
        rendered_any = False
        for key in ("translation", "summary", "transcript", "ocr_text"):
            if key in public_output and public_output[key]:
                val = str(public_output[key])
                if len(val) <= 4000:
                    excerpt = val
                else:
                    excerpt = val[:4000] + "\n... (truncated; see artifact)"

                lines.extend([f"### {key}", "", excerpt, ""])
                rendered_any = True
        if not rendered_any:
            lines.append("See produced artifacts and `report.json` for full details.")

    lines.extend(["", "## Assumptions", ""])
    if result.assumptions:
        for item in result.assumptions:
            lines.append(f"- {item}")
    else:
        lines.append("- No implicit assumptions were required.")

    lines.extend(["", "## Artifacts", ""])
    artifacts = workspace.list_artifacts() if workspace is not None else []
    has_audio = False
    if artifacts:
        for art in artifacts:
            art_path = Path(art.path)
            try:
                rel = art_path.relative_to(out_dir)
                link = f"./{rel.as_posix()}"
            except ValueError:
                link = art.path
            if art.kind == "audio":
                has_audio = True
            lines.append(
                f"- `{art.ref}` ({art.kind}, {art.bytes} bytes): "
                f"[{art_path.name}]({link})"
            )

    else:
        lines.append("- None.")

    if has_audio:
        lines.extend(
            [
                "",
                "> **AI-Generated Audio Notice:** All synthesized audio artifacts in this run "
                "are AI-generated using Bodhan Indic-Speak.",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def _write_outputs(
    out_dir: Path,
    task: str,
    result: AgentExecutionResult,
    trace: list[dict[str, Any]],
    budget: dict[str, Any],
    *,
    elapsed_seconds: float,
    workspace: Workspace | None = None,
) -> None:
    trace_path = out_dir / "trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as file:
        for event in trace:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    public_output = (
        {k: v for k, v in result.final_output.items() if not k.startswith("_")}
        if isinstance(result.final_output, dict)
        else result.final_output
    )
    report = {
        "task": task,
        "status": _derive_status(result),
        "success": result.success,
        "stopped_reason": result.stopped_reason,
        "total_cost_inr": result.total_cost_inr,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "total_time_ms": int(elapsed_seconds * 1000),
        "steps_executed": len(result.steps),
        "steps": [
            {
                "step_id": step.get("step_id"),
                "tool": step.get("tool"),
                "ok": step.get("result_summary", {}).get("ok", False),
                "cost_inr": step.get("cost_inr", 0.0),
                "cached": step.get("cached", False),
                "latency_ms": step.get("latency_ms", 0),
            }
            for step in result.steps
        ],
        "verification": result.verification,
        "assumptions": result.assumptions,
        "final_output": public_output,
        "budget": budget,
        "trace_path": str(trace_path),
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "result.md").write_text(
        _render_result_md(out_dir, task, result, elapsed_seconds, workspace),
        encoding="utf-8",
    )


def _budget_metadata(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "max_steps": args.max_steps,
        "max_cost_inr": args.max_cost_inr,
        "call_timeout_seconds": args.call_timeout_seconds,
        "scope": "Bodhan model API calls only; planner-provider charges are excluded.",
        "enforcement": (
            "Conservative estimate before each tool step; stop after reported spend exceeds "
            "the limit. Provider billing may exceed an estimate for a completed step."
        ),
    }


def _write_preflight_failure(
    out_dir: Path,
    task: str,
    preflight: dict[str, Any],
    trace: list[dict[str, Any]],
    args: argparse.Namespace,
    started_at: float,
    workspace: Workspace,
) -> int:
    issue = preflight["issues"][0]
    trace.append(
        {
            "event": "summary",
            "success": False,
            "stopped_reason": "preflight failed",
            "steps_executed": 0,
            "total_cost_inr": 0.0,
            "max_steps": args.max_steps,
            "max_cost_inr": args.max_cost_inr,
            "final_output_keys": ["error", "preflight"],
        }
    )
    result = AgentExecutionResult(
        False,
        [],
        {"error": issue, "preflight": preflight},
        0.0,
        stopped_reason="preflight failed",
    )
    _write_outputs(
        out_dir,
        task,
        result,
        trace,
        _budget_metadata(args),
        elapsed_seconds=time.monotonic() - started_at,
        workspace=workspace,
    )
    log.error("Task preflight failed; outputs written to %s: %s", out_dir, issue["message"])
    return 1


def _build_observations(result: AgentExecutionResult) -> list[dict[str, Any]]:
    step_outputs = (
        result.final_output.get("_step_outputs", {})
        if isinstance(result.final_output, dict)
        else {}
    )
    step_artifacts = (
        result.final_output.get("_step_artifacts", {})
        if isinstance(result.final_output, dict)
        else {}
    )
    observations: list[dict[str, Any]] = []
    for step in result.steps:
        step_id = step.get("step_id")
        outputs = step_outputs.get(step_id, {})
        compact_outputs: dict[str, Any] = {}
        for key, value in outputs.items():
            if isinstance(value, str) and len(value) > 300:
                compact_outputs[key] = Workspace.preview(value)
            else:
                compact_outputs[key] = value
        observations.append(
            {
                "step_id": step_id,
                "iteration": step.get("iteration"),
                "tool": step.get("tool"),
                "ok": step.get("result_summary", {}).get("ok"),
                "outputs": compact_outputs,
                "artifacts": [
                    {"ref": f"artifact:{art.get('ref')}", "kind": art.get("kind")}
                    for art in step_artifacts.get(step_id, [])
                ],
            }
        )
    return observations


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()

    started_at = time.monotonic()
    parser = argparse.ArgumentParser(description="Bodhan budgeted task agent")
    parser.add_argument("--task", required=True, help="Plain-language task description.")
    parser.add_argument("--files", required=True, nargs="+", help="Input file(s) or directory.")
    parser.add_argument("--out", default="./out", help="Output directory.")
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum model-tool steps.")
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum estimated Bodhan API cost in INR; planner-provider charges are excluded.",
    )
    parser.add_argument(
        "--call-timeout-seconds",
        type=float,
        default=180.0,
        help="Hard timeout for each Bodhan API call, including retries.",
    )
    parser.add_argument(
        "--planning-timeout-seconds",
        type=float,
        default=None,
        help="Hard timeout for the planning LLM call (defaults to LLM_TIMEOUT_S or 60).",
    )
    args = parser.parse_args()

    if args.max_steps < 1:
        parser.error("--max-steps must be at least 1")
    if not math.isfinite(args.max_cost_inr) or args.max_cost_inr < 0:
        parser.error("--max-cost-inr must be a finite non-negative number")
    if not math.isfinite(args.call_timeout_seconds) or args.call_timeout_seconds <= 0:
        parser.error("--call-timeout-seconds must be a finite positive number")
    if args.planning_timeout_seconds is not None and (
        not math.isfinite(args.planning_timeout_seconds) or args.planning_timeout_seconds <= 0
    ):
        parser.error("--planning-timeout-seconds must be a finite positive number")

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    input_root = out_dir / ".scratch" / "inputs"
    input_root.mkdir(parents=True, exist_ok=True)

    available_files: list[str] = []
    for raw_file in args.files:
        input_path = Path(raw_file).expanduser().resolve()
        if not input_path.exists():
            parser.error(f"--files path does not exist: {input_path}")
        if not input_path.is_file() and not input_path.is_dir():
            parser.error("--files must point to a file or directory")
        sources = [input_path] if input_path.is_file() else sorted(input_path.rglob("*"))
        for source in sources:
            if not source.is_file() or not _safe_input(source):
                continue
            relative = Path(source.name) if input_path.is_file() else source.relative_to(input_path)
            destination = input_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            available_files.append(str(Path(".scratch") / "inputs" / relative))
    if not available_files:
        parser.error("--files did not contain any readable input files")

    workspace = Workspace(out_dir, out_dir=out_dir, input_dirs=[input_root])
    preflight = inspect_task_inputs(available_files, workspace)
    trace: list[dict[str, Any]] = [
        {
            "event": "preflight",
            "result_summary": preflight,
        }
    ]
    if not preflight["ok"]:
        return _write_preflight_failure(
            out_dir, args.task, preflight, trace, args, started_at, workspace
        )

    config = ClientConfig.from_env()
    config = replace(
        config,
        attempt_timeout_s=min(config.attempt_timeout_s, args.call_timeout_seconds),
        total_timeout_s=args.call_timeout_seconds,
    )
    ledger = CostLedger()
    cache = DiskCache.from_env()
    plan: list[dict[str, Any]] = []
    deliverables: list[dict[str, Any]] = []
    refusal: dict[str, Any] | None = None
    needs_followup = False
    client: BodhanClient | None = None
    planner: AgentPlanner | None = None
    planning_started = time.monotonic()
    try:
        log.info("Starting task agent for %r", args.task)
        try:
            planner = AgentPlanner(timeout_s=args.planning_timeout_seconds)
            planned = planner.plan_task(
                args.task,
                available_files,
                input_manifest=preflight["inspected_inputs"],
            )
            if isinstance(planned, TaskPlan):
                plan = planned.steps
                deliverables = planned.deliverables
                refusal = planned.refusal
                needs_followup = planned.needs_followup
            elif isinstance(planned, list):
                plan = planned
            else:
                raise TypeError("Planner must return a TaskPlan.")
            planner_usage = planner.last_usage
            trace.append(
                {
                    "event": "planner",
                    "latency_ms": int((time.monotonic() - planning_started) * 1000),
                    "cost_inr": planner_usage.get("reported_cost_inr"),
                    "cost_status": (
                        "reported by provider"
                        if planner_usage.get("reported_cost_inr") is not None
                        else "unknown; planner-provider cost is outside the Bodhan INR budget"
                    ),
                    "usage_tokens": {
                        "prompt": planner_usage.get("prompt_tokens"),
                        "completion": planner_usage.get("completion_tokens"),
                    },
                    "catalog_version": getattr(planner, "tool_catalog", {}).get("version"),
                    "available_tools": [
                        tool["name"]
                        for tool in getattr(planner, "tool_catalog", {}).get("tools", [])
                        if tool.get("available")
                    ],
                    "result_summary": {
                        "ok": refusal is None,
                        "plan_steps": len(plan),
                        "deliverable_count": len(deliverables),
                        "needs_followup": needs_followup,
                        "refusal_code": refusal.get("code") if refusal else None,
                    },
                }
            )
        except Exception as exc:
            error = (
                exc.to_dict()
                if isinstance(exc, ToolError)
                else {
                    "code": ErrorCode.UPSTREAM_ERROR.value,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
            trace.extend(
                [
                    {
                        "event": "planner",
                        "latency_ms": int((time.monotonic() - planning_started) * 1000),
                        "cost_inr": None,
                        "cost_status": (
                            "unknown; planner-provider cost is outside the Bodhan INR budget"
                        ),
                        "result_summary": {"ok": False, "error": error},
                    },
                    {
                        "event": "summary",
                        "success": False,
                        "stopped_reason": "planning failed",
                        "steps_executed": 0,
                        "total_cost_inr": 0.0,
                        "max_steps": args.max_steps,
                        "max_cost_inr": args.max_cost_inr,
                        "final_output_keys": [],
                    },
                ]
            )
            result = AgentExecutionResult(
                False, [], {"error": error}, 0.0, stopped_reason="planning failed"
            )
            _write_outputs(
                out_dir,
                args.task,
                result,
                trace,
                _budget_metadata(args),
                elapsed_seconds=time.monotonic() - started_at,
                workspace=workspace,
            )
            log.error("Task planning failed; outputs written to %s", out_dir)
            return 1

        plan_validation = validate_plan(
            plan,
            workspace,
            getattr(planner, "tool_catalog", None),
            refusal=refusal,
            deliverables=deliverables,
            input_manifest=preflight["inspected_inputs"],
            needs_followup=needs_followup,
        )
        trace.append(
            {
                "event": "plan_validation",
                "result_summary": plan_validation,
            }
        )
        if not plan_validation["ok"]:
            issue = plan_validation["issues"][0]
            task_refused = isinstance(refusal, dict) and issue["code"] == refusal.get("code")
            stopped_reason = "task refused" if task_refused else "plan validation failed"
            if task_refused:
                trace.append(
                    {
                        "event": "task_refusal",
                        "result_summary": issue,
                    }
                )
            trace.append(
                {
                    "event": "summary",
                    "success": False,
                    "stopped_reason": stopped_reason,
                    "steps_executed": 0,
                    "total_cost_inr": 0.0,
                    "max_steps": args.max_steps,
                    "max_cost_inr": args.max_cost_inr,
                    "final_output_keys": [
                        "error",
                        "preflight",
                        "plan_validation",
                        *(["refusal"] if task_refused else []),
                    ],
                }
            )
            result = AgentExecutionResult(
                False,
                [],
                {
                    "error": issue,
                    "preflight": preflight,
                    "plan_validation": plan_validation,
                    **({"refusal": refusal} if task_refused else {}),
                },
                0.0,
                stopped_reason=stopped_reason,
            )
            _write_outputs(
                out_dir,
                args.task,
                result,
                trace,
                _budget_metadata(args),
                elapsed_seconds=time.monotonic() - started_at,
                workspace=workspace,
            )
            log.error(
                "Task %s; outputs written to %s: %s",
                "refused" if task_refused else "plan validation failed",
                out_dir,
                issue["message"],
            )
            return 1

        client = BodhanClient(config)
        log.info("Executing %d planned steps", len(plan))
        agent = BodhanAgentLoop(
            workspace,
            client=client,
            ledger=ledger,
            cache=cache,
            max_steps=args.max_steps,
            max_cost_inr=args.max_cost_inr,
            english_summarizer=planner.summarize_english,
        )
        result = agent.execute_pipeline(
            plan,
            deliverables=deliverables,
            input_items=preflight["inspected_inputs"],
        )
        trace.extend(result.trace)

        followup_round = 0
        while (
            needs_followup
            and result.success
            and followup_round < _MAX_FOLLOWUP_ROUNDS
            and hasattr(planner, "replan_task")
        ):
            followup_round += 1
            remaining_steps = args.max_steps - len(result.steps)
            remaining_budget = max(0.0, round(args.max_cost_inr - result.total_cost_inr, 6))
            if remaining_steps < 1:
                result.success = False
                result.stopped_reason = "maximum step count reached before follow-up"
                break
            observations = _build_observations(result)
            followup_plan = planner.replan_task(
                args.task,
                available_files,
                preflight["inspected_inputs"],
                observations,
            )
            followup_validation = validate_plan(
                followup_plan.steps,
                workspace,
                getattr(planner, "tool_catalog", None),
                refusal=followup_plan.refusal,
                deliverables=followup_plan.deliverables,
                input_manifest=preflight["inspected_inputs"],
                needs_followup=followup_plan.needs_followup,
            )
            trace.append({"event": "plan_validation", "result_summary": followup_validation})
            if not followup_validation["ok"]:
                issue = followup_validation["issues"][0]
                result.success = False
                result.stopped_reason = (
                    "task refused"
                    if isinstance(followup_plan.refusal, dict)
                    else "follow-up plan validation failed"
                )
                result.final_output["error"] = issue
                break
            followup_agent = BodhanAgentLoop(
                workspace,
                client=client,
                ledger=ledger,
                cache=cache,
                max_steps=remaining_steps,
                max_cost_inr=remaining_budget,
                english_summarizer=planner.summarize_english,
            )
            next_res = followup_agent.execute_pipeline(
                followup_plan.steps,
                deliverables=followup_plan.deliverables,
                input_items=preflight["inspected_inputs"],
            )
            trace.extend(next_res.trace)
            result.steps.extend(next_res.steps)
            result.total_cost_inr = round(result.total_cost_inr + next_res.total_cost_inr, 6)
            result.assumptions = list(dict.fromkeys([*result.assumptions, *next_res.assumptions]))
            for k in ("audio_checks", "translation_checks", "ocr_checks"):
                result.verification.setdefault(k, []).extend(next_res.verification.get(k, []))
            result.final_output.update(next_res.final_output)
            result.success = next_res.success
            result.stopped_reason = next_res.stopped_reason
            needs_followup = followup_plan.needs_followup

        _write_outputs(
            out_dir,
            args.task,
            result,
            trace,
            _budget_metadata(args),
            elapsed_seconds=time.monotonic() - started_at,
            workspace=workspace,
        )
        log.info("Task agent finished; outputs written to %s", out_dir)
        return 0 if result.success else 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())