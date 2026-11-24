"""Status decision and the run's outputs: result.md, report.json and the final trace summary.

The status is decided by code from facts (verification, budgets, errors), not taken from the
LLM. The LLM's claimed status can only be lowered, never raised:

    completed          finish=completed and every error-severity verification check passed
    partial            something useful was produced but not everything, or verification failed
    infeasible         the task cannot be done with Bodhan's tools (explained, nothing fabricated)
    budget_exhausted   the rupee budget stopped the run before completion
    incomplete         the step/time budget or an LLM outage stopped the run
    failed             nothing useful was produced
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .budget import Budget
from .trace import Trace

if TYPE_CHECKING:
    from .agent import RunState

LATENCY_TARGET_S = {"single_step": 30, "multi_step": 300}


def decide_status(state: RunState) -> str:
    f = state.finish
    ver_ok = bool(state.verification and state.verification.get("passed"))
    if f and f.get("status") == "infeasible":
        return "infeasible"
    if f is None:
        if state.budget_hit:
            return "budget_exhausted"
        return "incomplete" if state.stop_reason in {"max_steps", "max_time", "llm_error", "no_action"} else "failed"
    claimed = f.get("status")
    if claimed == "completed" and ver_ok and (state.artifacts or f.get("answer")):
        return "completed"
    if state.budget_hit and claimed != "completed":
        return "budget_exhausted"
    if claimed == "failed" and not state.artifacts:
        return "failed"
    return "partial" if (state.artifacts or f.get("answer")) else "failed"


def _rel_links(state: RunState) -> list[str]:
    lines = []
    for a in state.artifacts:
        label = " (AI-generated speech)" if a.get("ai_generated_audio") else ""
        lines.append(f"- [{a['file']}]({a['file']}){label} — {a.get('description') or ''}")
    return lines


def _paid_tool_steps(state: RunState) -> int:
    return len({e["step"] for e in state.tool_log if e["tool"] not in {"read_text_file", "write_text_file", "inspect_media",
                                                                        "list_voices"}})


def write_outputs(state: RunState, budget: Budget, status: str, trace: Trace) -> None:
    out = state.out_dir
    f = state.finish or {}
    ver = state.verification or {"passed": False, "checks": []}
    model_calls = sum(e.get("model_calls", 0) for e in state.tool_log)
    cache_hits = sum(e.get("cache_hits", 0) for e in state.tool_log)
    multi = _paid_tool_steps(state) > 1
    latency_target = LATENCY_TARGET_S["multi_step" if multi else "single_step"]
    targets = {
        "honest_failure": {"met": status != "completed" or ver.get("passed", False),
                           "detail": "status decided from verification and budgets, not from the LLM's claim"},
        "verification": {"met": all(c["passed"] for c in ver.get("checks", []) if c["name"] in
                                    {"speech_back_transcription", "translation_checks"}),
                         "detail": "every generated audio file and translation checked"},
        "no_waste": {"met": True, "detail": f"{model_calls} model requests, {cache_hits} served from cache; "
                                            "identical tool calls are memoised and model calls are content-cached"},
        "budget": {"met": budget.spent_inr <= budget.max_cost_inr + 1e-9,
                   "detail": f"₹{budget.spent_inr:.4f} of ₹{budget.max_cost_inr:.2f}"},
        "latency": {"met": budget.elapsed_s <= latency_target,
                    "detail": f"{budget.elapsed_s:.1f}s ({'multi' if multi else 'single'}-step target {latency_target}s)"},
    }
    missed = {k: v["detail"] for k, v in targets.items() if not v["met"]}
    rep: dict[str, Any] = {
        "status": status,
        "agent_claimed_status": f.get("claimed_status", f.get("status")),
        "stop_reason": state.stop_reason or ("finished" if state.finish else None),
        "task": state.task,
        "input_files": state.files,
        "answer": f.get("answer"),
        "artifacts": state.artifacts,
        "assumptions": state.assumptions,
        "plan": state.plan,
        "steps": {"used": budget.steps, "max": budget.max_steps, "tool_calls": len(state.tool_log),
                  "llm_calls": state.llm_calls},
        "cost_inr": {"total": round(budget.spent_inr, 4), "max": budget.max_cost_inr,
                     "by_tool": budget.cost_by_tool, "by_model": budget.cost_by_model},
        "time_s": round(budget.elapsed_s, 2),
        "llm_tokens": state.llm_tokens,
        "verification": ver,
        "quality_targets": targets,
        "missed_targets": missed,
        "model_requests": model_calls,
        "cache_hits": cache_hits,
        "errors": state.errors,
        "prompt_injection": state.injection,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "agent_version": __version__,
    }
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md: list[str] = [f"# Task result: {status.replace('_', ' ')}", "", f"**Task:** {state.task}", ""]
    if state.files:
        md += [f"**Inputs:** {', '.join(f'`{x}`' for x in state.files)}", ""]
    md += ["## Answer", "", f.get("answer") or _fallback_answer(state, status), ""]
    md += ["## Artifacts", ""] + (_rel_links(state) or ["None. No output was produced, so none is claimed."]) + [""]
    if any(a.get("ai_generated_audio") for a in state.artifacts):
        md += ["> Audio artifacts are AI-generated speech (Bodhan Indic-Speak, fixed voice library), not human "
               "recordings.", ""]
    md += ["## Assumptions", ""] + ([f"- {a}" for a in state.assumptions] or ["- None."]) + [""]
    overall = "not applicable (nothing was produced)" if not ver.get("checks") else ("passed" if ver.get("passed") else "not passed")
    md += ["## Verification", "", f"Overall: **{overall}**", "",
           "| Check | File | Result | Detail |", "|---|---|---|---|"]
    for c in ver.get("checks", []):
        res = "pass" if c["passed"] else ("warn" if c["severity"] == "warning" else "FAIL")
        md.append(f"| {c['name']} | {c.get('file', '')} | {res} | {str(c.get('detail', '')).replace('|', '/')[:160]} |")
    md += ["", "## What was done", ""]
    if state.plan:
        md += [f"Plan: {state.plan.get('understanding', '')}", ""]
    for e in state.tool_log:
        flag = "ok" if e["ok"] else "error"
        md.append(f"{e['step']}. `{e['tool']}` ({flag}, {e['latency_ms']} ms, ₹{e['cost_inr']:.4f}) — "
                  f"{str(e['result_summary'])[:180]}")
    if not state.tool_log:
        md.append("No tools were called.")
    md += ["", "## Cost and time", "",
           f"- Bodhan cost: ₹{budget.spent_inr:.4f} of ₹{budget.max_cost_inr:.2f} budget "
           f"({model_calls} model requests, {cache_hits} cache hits)",
           f"- Steps: {budget.steps} of {budget.max_steps}; LLM calls: {state.llm_calls}",
           f"- Wall time: {budget.elapsed_s:.1f}s", ""]
    if state.injection:
        md += ["## Untrusted content", "", "Input content contained instruction-like text. It was treated as data "
               "and not followed:", ""] + [f"- {i['where']}: “{i['excerpts'][0][:120]}”" for i in state.injection] + [""]
    if state.errors:
        md += ["## Problems encountered", ""] + [f"- {e.get('code')}: {str(e.get('message'))[:200]}"
                                               for e in state.errors[:15]] + [""]
    if missed:
        md += ["## Missed quality targets", ""] + [f"- {k}: {v}" for k, v in missed.items()] + [""]
    md += ["Full step-by-step record: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).", ""]
    (out / "result.md").write_text("\n".join(md), encoding="utf-8")
    trace.write("summary", budget.steps, status=status, cost_inr=round(budget.spent_inr, 4),
                time_s=round(budget.elapsed_s, 2), artifacts=[a["file"] for a in state.artifacts],
                verification_passed=ver.get("passed"), missed_targets=missed)


def _fallback_answer(state: RunState, status: str) -> str:
    reasons = {
        "budget_exhausted": "The rupee budget ran out before the task could be completed.",
        "incomplete": f"The run stopped early ({state.stop_reason}) before the task was finished.",
        "failed": "The task could not be completed.",
    }
    base = reasons.get(status, "The run ended without a final answer.")
    errs = "; ".join(f"{e.get('code')}: {str(e.get('message'))[:120]}" for e in state.errors[-3:])
    return base + (f" Last problems: {errs}" if errs else "") + " Nothing was fabricated; see the steps below."


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
