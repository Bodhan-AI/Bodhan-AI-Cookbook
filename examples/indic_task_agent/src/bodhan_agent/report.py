"""Writes result.md (for people) and report.json (for machines) from the final run state."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .agent import RunState

STATUS_LABEL = {"success": "✅ success", "partial": "⚠️ partial", "impossible": "⛔ impossible", "failed": "❌ failed"}
AUDIO_EXT = {".mp3", ".wav", ".ogg"}


def _rel(path: str, base: Path) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def build_report(st: RunState, llm_model: str) -> dict[str, Any]:
    elapsed = round(st.elapsed, 2)
    multi_step = st.steps > 1
    latency_target = 300 if multi_step else 30
    paid_steps = [s for s in st.step_log if s.ok and s.cost_inr > 0]
    cached = sum(int(v.get("cached", 0)) for v in st.cost_by_model.values())
    return {
        "status": st.status,
        "stop_reason": st.stop_reason,
        "task": st.task,
        "answer_en": st.answer_en,
        "artifacts": [
            {
                "path": _rel(a.path, st.out_dir),
                "description": a.description,
                "produced_by": a.tool,
                "verified": a.verified,
                "verification": a.verification,
                **({"label": "AI-generated speech (Bodhan Indic-Speak)"} if Path(a.path).suffix in AUDIO_EXT else {}),
            }
            for a in st.artifacts
        ],
        "assumptions": st.assumptions,
        "notes": st.notes,
        "security_flags": st.security_flags,
        "steps": {
            "tool_calls": st.steps,
            "max_steps": st.budgets.max_steps,
            "llm_calls": st.llm_calls,
            "duplicate_calls_blocked": st.duplicate_calls_blocked,
        },
        "cost": {
            "total_inr": round(st.spent, 4),
            "max_cost_inr": st.budgets.max_cost_inr,
            "by_model": st.cost_by_model,
            "llm_tokens": st.llm_usage,
            "llm_model": llm_model,
            "note": "Bodhan costs are computed from the published hosted prices; LLM spend is reported in tokens.",
        },
        "time": {"elapsed_s": elapsed, "max_wall_s": st.budgets.max_wall_s},
        "verification": [
            {"artifact": _rel(a.path, st.out_dir), "verified": a.verified, **a.verification} for a in st.artifacts
        ],
        "plan": st.plan,
        "quality_targets": {
            "budget_respected": st.spent <= st.budgets.max_cost_inr + 1e-9,
            "all_artifacts_verified": all(a.verified for a in st.artifacts),
            "honest_failure": st.status not in {"impossible", "failed"} or not st.artifacts,
            "latency_target_s": latency_target,
            "latency_met": elapsed <= latency_target,
            "no_repeat_paid_calls": {
                "duplicates_blocked": st.duplicate_calls_blocked,
                "cache_hits": cached,
                "paid_tool_calls": len(paid_steps),
            },
            "misses": _misses(st, elapsed, latency_target),
        },
    }


def _misses(st: RunState, elapsed: float, target: float) -> list[str]:
    out = []
    if elapsed > target:
        out.append(
            f"Latency {elapsed:.0f}s exceeded the {target}s target (dominated by Bodhan/LLM response times; see trace latency_ms)."
        )
    if any(not a.verified for a in st.artifacts):
        out.append("Some artifacts did not pass verification; see the verification section.")
    if st.stop_reason in {"budget_exhausted", "step_limit", "time_limit", "llm_call_limit"}:
        out.append(f"Stopped early: {st.stop_reason}.")
    return out


def write_outputs(st: RunState, llm_model: str) -> dict[str, Any]:
    report = build_report(st, llm_model)
    (st.out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (st.out_dir / "result.md").write_text(render_markdown(st, report), encoding="utf-8")
    return report


def render_markdown(st: RunState, report: dict[str, Any]) -> str:
    lines = ["# Task result", "", f"**Task:** {st.task}", "", f"**Status:** {STATUS_LABEL.get(st.status, st.status)}"]
    if st.stop_reason:
        lines.append(f"  \n**Stopped because:** {st.stop_reason}")
    lines += ["", "## Answer", "", st.answer_en or "_(none)_", ""]

    lines += ["## Artifacts", ""]
    if st.artifacts:
        for a in st.artifacts:
            rel = _rel(a.path, st.out_dir)
            badge = "verified" if a.verified else "NOT verified"
            label = (
                " — 🔊 *AI-generated speech (Bodhan Indic-Speak), not a human recording*"
                if Path(a.path).suffix in AUDIO_EXT
                else ""
            )
            lines.append(f"- [{Path(a.path).name}]({rel}) — {a.description} ({badge}){label}")
    else:
        lines.append(
            "_No output files were produced._"
            if st.status in {"impossible", "failed"}
            else "_No files were requested or delivered._"
        )
    lines.append("")

    lines += ["## Assumptions", ""]
    lines += [f"- {a}" for a in st.assumptions] or ["_None._"]
    lines.append("")

    lines += ["## Verification", ""]
    if st.artifacts:
        for a in st.artifacts:
            details = ", ".join(f"{k}={v}" for k, v in a.verification.items() if k != "warnings")
            lines.append(f"- **{Path(a.path).name}**: {'pass' if a.verified else 'FAIL'} — {details}")
    else:
        lines.append("_Nothing to verify._")
    lines.append("")

    lines += ["## What was done", "", "| # | Tool | Why | Result | Cost (₹) | Latency |", "|---|---|---|---|---|---|"]
    for s in st.step_log:
        result = "ok" if s.ok else f"error: {s.error_code}"
        why = s.why.replace("|", "/")[:120]
        lines.append(
            f"| {s.step or 'pre'} | `{s.tool}` | {why} | {result} | {s.cost_inr:.4f} | {s.latency_ms / 1000:.1f}s |"
        )
    lines.append("")

    if st.notes or st.security_flags:
        lines += ["## Notes", ""]
        lines += [f"- 🛡️ {n}" for n in st.security_flags]
        lines += [f"- {n}" for n in st.notes]
        lines.append("")

    cost = report["cost"]
    lines += [
        "## Cost and time",
        "",
        f"- Bodhan cost: **₹{cost['total_inr']:.4f}** of ₹{cost['max_cost_inr']} budget",
        f"- By model: {json.dumps(cost['by_model'])}",
        f"- Tool calls: {st.steps}/{st.budgets.max_steps}; LLM calls: {st.llm_calls} ({cost['llm_tokens']} tokens, model {cost['llm_model']})",
        f"- Wall time: {report['time']['elapsed_s']}s",
        "",
        "Full step-by-step record: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).",
        "",
    ]
    return "\n".join(lines)
