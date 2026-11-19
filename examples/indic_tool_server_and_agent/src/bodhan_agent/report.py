"""Report generator: produces result.md and report.json in the output directory."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_result_md(state: Any, out_dir: str | Path) -> str:
    """
    Write out/result.md:
    Summary of what was done, deliverables, assumptions, links to artifacts,
    or honest failure / impossible explanation.
    """
    out_path = Path(out_dir) / "result.md"
    lines = []

    lines.append("# Bodhan Task Agent Execution Result\n")
    lines.append(f"**Task:** {state.task}\n")
    lines.append(f"**Status:** `{state.status}`\n")

    if state.stop_reason:
        lines.append(f"**Stop Reason:** {state.stop_reason}\n")

    if state.goal:
        lines.append(f"**Goal:** {state.goal}\n")

    # Assumptions
    if state.assumptions:
        lines.append("## Assumptions Made\n")
        for a in state.assumptions:
            lines.append(f"- {a}")
        lines.append("")

    # Artifacts / Deliverables
    lines.append("## Deliverables & Artifacts\n")
    if state.artifacts:
        for art in state.artifacts:
            p = Path(art.path)
            lines.append(f"- **{art.name}** (`{art.kind}`): [{p.name}](file://{p.resolve()})")
        lines.append("")
    else:
        if state.status == "impossible":
            lines.append("*No artifacts produced because the task is impossible as requested.* \n")
        elif state.status == "budget_exhausted":
            lines.append("*No final artifacts completed prior to budget exhaustion.*\n")
        else:
            lines.append("*No file artifacts produced.*\n")

    # What was done
    lines.append("## Execution Steps\n")
    if state.plan:
        for idx, step in enumerate(state.plan, 1):
            status_emoji = "✅" if step.status == "done" else ("❌" if step.status == "failed" else "⏳")
            lines.append(
                f"{idx}. {status_emoji} **Step `{step.id}`** (`{step.tool}`): {step.purpose or 'No purpose specified'}\n"
                f"   - Status: `{step.status}` | Cost: ₹{step.cost_inr:.4f} | Latency: {step.latency_ms or 0:.0f}ms"
            )
            if step.error:
                lines.append(f"   - Error: `{step.error}`")
        lines.append("")
    else:
        lines.append("*No plan steps executed.*\n")

    # Verifications
    if state.verifications:
        lines.append("## Verification Results\n")
        for v in state.verifications:
            v_status = v.get("status", "unknown")
            v_check = v.get("check", "check")
            v_details = v.get("details") or v.get("reason") or json.dumps(v)
            lines.append(f"- **{v_check}**: `{v_status}` - {v_details}")
        lines.append("")

    # Summary Stats
    lines.append("## Summary Statistics\n")
    lines.append(f"- **Total Steps Used:** {state.steps_used}")
    lines.append(f"- **Total Cost:** ₹{state.spent_inr:.4f}")
    lines.append(f"- **Elapsed Time:** {state.elapsed_s:.2f}s\n")

    content = "\n".join(lines)
    out_path.write_text(content, encoding="utf-8")
    logger.info("Wrote result.md to %s", out_path)
    return str(out_path)


def write_report_json(state: Any, out_dir: str | Path) -> str:
    """
    Write out/report.json:
    Machine-readable execution details, costs, verifications, and quality targets.
    """
    out_path = Path(out_dir) / "report.json"

    missed_targets = []
    if state.status == "budget_exhausted":
        missed_targets.append("Task stopped prematurely due to budget constraints.")
    elif state.status == "failed":
        missed_targets.append(f"Task failed: {state.stop_reason}")

    for v in state.verifications:
        if v.get("status") == "failed":
            missed_targets.append(f"Verification failure in {v.get('check')}: {v.get('reason')}")

    steps_data = []
    for s in state.plan:
        steps_data.append({
            "id": s.id,
            "tool": s.tool,
            "status": s.status,
            "purpose": s.purpose,
            "cost_inr": round(s.cost_inr, 4),
            "latency_ms": round(s.latency_ms, 2) if s.latency_ms else None,
            "error": s.error,
        })

    artifacts_data = []
    for a in state.artifacts:
        artifacts_data.append({
            "name": a.name,
            "path": a.path,
            "kind": a.kind,
            "produced_by": a.produced_by,
        })

    report = {
        "task": state.task,
        "goal": state.goal,
        "status": state.status,
        "stop_reason": state.stop_reason,
        "total_cost_inr": round(state.spent_inr, 4),
        "spent_inr": round(state.spent_inr, 4),
        "total_time_seconds": round(state.elapsed_s, 2),
        "steps_used": state.steps_used,
        "replan_count": state.replan_count,
        "assumptions": state.assumptions,
        "artifacts": artifacts_data,
        "steps": steps_data,
        "verifications": state.verifications,
        "missed_targets": missed_targets,
    }

    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote report.json to %s", out_path)
    return str(out_path)
