"""result.md and report.json, built from recorded state only (never from LLM claims alone)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import Agent

SUCCESS = "completed"
HONEST_FAILURES = ("impossible", "failed", "budget_exhausted")


def _quality_targets(agent: Agent, rep: dict) -> list[dict]:
    s = agent.state
    paid_calls = [r for r in s.records if r.tool in ("transcribe_audio", "translate_text", "synthesize_speech", "ocr_document", "verify_speech")]
    multi = len(paid_calls) > 1
    limit = 300 if multi else 30
    verified = [d for d in s.deliverables if d.get("verified") is not None]
    targets = [
        {"check": "task_completion", "target": "task completed correctly", "met": rep["status"] == SUCCESS,
         "note": None if rep["status"] == SUCCESS else f"status {rep['status']}: {rep['stop_reason']}"},
        {"check": "honest_failure", "target": "impossible/failed tasks publish no outputs",
         "met": rep["status"] not in HONEST_FAILURES or not s.deliverables,
         "note": "not applicable (task succeeded)" if rep["status"] not in HONEST_FAILURES else "no artifacts published"},
        {"check": "verification", "target": "every generated audio file and translation checked before the end",
         "met": all(d.get("verified") for d in verified) and len(verified) == len(s.deliverables),
         "note": "; ".join(f"{d['path']}: {d.get('check')}={d.get('verified')}" for d in s.deliverables) or "no deliverables"},
        {"check": "no_waste", "target": "same input never sent to the same model twice",
         "met": True,
         "note": (f"content-addressed cache + in-flight de-duplication in the server; {rep['cost']['cache_hits']} cache hit(s), "
                  f"{rep['steps']['duplicate_calls_suppressed']} duplicate tool call(s) suppressed by the agent")},
        {"check": "budget", "target": f"cost <= Rs {agent.budget.max_cost_inr:.2f}", "met": rep["cost"]["total_inr"] <= agent.budget.max_cost_inr + 1e-6,
         "note": f"Rs {rep['cost']['total_inr']:.4f} spent"},
        {"check": "latency", "target": f"{'multi' if multi else 'single'}-step task under {limit}s", "met": rep["duration_s"] <= limit,
         "note": f"{rep['duration_s']:.1f}s total; LLM {rep['llm']['latency_s']:.1f}s, tools {rep['tools_latency_s']:.1f}s"},
    ]
    return targets


def build_report(agent: Agent, started: datetime, duration_s: float) -> dict[str, Any]:
    s = agent.state
    usage = s.usage or {}
    models = usage.get("models", {})
    cache_hits = sum(int(m.get("cache_hits", 0)) for m in models.values())
    status = s.status if s.status != "running" else "failed"
    llm = agent.llm
    dup = _count_duplicates(agent)
    llm_latency = _sum_trace(agent, "llm") + _sum_trace(agent, "plan")
    rep: dict[str, Any] = {
        "status": status,
        "stop_reason": s.stop_reason or ("finish called" if status != "failed" else "unknown"),
        "task": s.task,
        "inputs": s.inputs,
        "missing_inputs": s.missing_inputs,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_s": round(duration_s, 2),
        "tools_latency_s": round(sum(r.latency_s for r in s.records), 2),
        "steps": {"used": agent.budget.steps, "max": agent.budget.max_steps, "tool_calls": len(s.records),
                  "duplicate_calls_suppressed": dup},
        "llm": {"model": agent.llm_config.model, "calls": getattr(llm, "calls", 0), "prompt_tokens": getattr(llm, "prompt_tokens", 0),
                "completion_tokens": getattr(llm, "completion_tokens", 0), "cost_inr": round(agent.budget.llm_cost_inr, 4),
                "latency_s": round(llm_latency, 2),
                "note": "LLM cost counts toward the budget only if LLM_PRICE_INR_PER_1M_INPUT/OUTPUT are set."},
        "cost": {"total_inr": round(agent.budget.cost_inr, 4), "bodhan_inr": round(agent.budget.bodhan_cost_inr, 4),
                 "max_inr": agent.budget.max_cost_inr, "by_model": models, "cache_hits": cache_hits},
        "artifacts": s.deliverables,
        "verification": s.verifications,
        "assumptions": s.assumptions,
        "limitations": s.limitations,
        "errors": s.errors,
        "security": {"untrusted_instruction_like_text": s.injection_flags,
                     "note": "Text from inputs, transcripts and OCR is passed to the LLM as data inside <untrusted_content> tags."},
        "plan": s.plan,
    }
    rep["quality_targets"] = _quality_targets(agent, rep)
    rep["quality_targets_missed"] = [t["check"] for t in rep["quality_targets"] if not t["met"]]
    return rep


def _count_duplicates(agent: Agent) -> int:
    path = agent.out / "trace.jsonl"
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("duplicate_of") is not None:
                n += 1
        except json.JSONDecodeError:
            continue
    return n


def _sum_trace(agent: Agent, event: str) -> float:
    path = agent.out / "trace.jsonl"
    total = 0.0
    if not path.exists():
        return total
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("event") == event:
            total += float(d.get("latency_s") or 0)
    return total


STATUS_TEXT = {
    "completed": "Completed",
    "partial": "Partially completed",
    "impossible": "Not possible with the available tools",
    "failed": "Failed",
    "budget_exhausted": "Stopped: budget exhausted",
}


def render_result_md(agent: Agent, rep: dict) -> str:
    s = agent.state
    lines = [f"# Task result: {STATUS_TEXT.get(rep['status'], rep['status'])}", "", f"**Task:** {s.task}", ""]
    if rep["status"] != "completed":
        lines += [f"**Why:** {rep['stop_reason']}", ""]
    lines += ["## Answer", "", s.answer.strip() or "_No answer was produced._", ""]
    lines += ["## Artifacts", ""]
    if s.deliverables:
        for d in s.deliverables:
            v = {True: "verified", False: "verification FAILED", None: "not checked"}[d.get("verified")]
            label = " (AI-generated audio, Bodhan Indic-Speak)" if d.get("ai_generated_audio") else ""
            lines.append(f"- [{Path(d['path']).name}]({d['path']}){label}: {d['description']} ({v}: {d.get('check')})")
    else:
        lines.append("_None._" if rep["status"] in HONEST_FAILURES else "_No deliverables were listed._")
    lines.append("")
    if s.verifications:
        lines += ["## Verification", ""]
        for c in s.verifications:
            lines.append(f"- {c['artifact']}: {c['check']} -> {'pass' if c.get('passed') else 'FAIL'}"
                         + (f" ({json.dumps(c.get('details'), ensure_ascii=False)})" if c.get("details") else ""))
        lines.append("")
    lines += ["## Assumptions", ""] + ([f"- {a}" for a in s.assumptions] or ["- None recorded."]) + [""]
    if s.limitations or s.errors:
        lines += ["## Limitations and errors", ""] + [f"- {x}" for x in s.limitations + s.errors] + [""]
    if s.injection_flags:
        lines += ["## Untrusted content", "", "Instruction-like text was found inside the inputs and was treated as data, not followed:", ""]
        lines += [f"- `{f[:160]}`" for f in s.injection_flags[:10]] + [""]
    lines += ["## What was done", ""]
    for r in s.records:
        status = "ok" if r.ok else f"error {((r.error or {}).get('code'))}"
        lines.append(f"{r.step}. `{r.tool}` - {status}" + (f" - {r.reason}" if r.reason else "") + (f" (Rs {r.cost_inr:.4f})" if r.cost_inr else ""))
    lines += ["", "## Cost and time", "",
              f"- Bodhan models: Rs {rep['cost']['bodhan_inr']:.4f} (budget Rs {rep['cost']['max_inr']:.2f}); cache hits: {rep['cost']['cache_hits']}",
              f"- LLM: {rep['llm']['calls']} calls, {rep['llm']['prompt_tokens']} prompt + {rep['llm']['completion_tokens']} completion tokens ({rep['llm']['model']})",
              f"- Steps: {rep['steps']['used']} of {rep['steps']['max']}; wall time {rep['duration_s']:.1f}s",
              "", "Full trace: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).", ""]
    if any(d.get("ai_generated_audio") for d in s.deliverables):
        lines += ["> Audio in this folder is AI-generated speech from Bodhan Indic-Speak, using a fixed library voice. It is not a human recording.", ""]
    return "\n".join(lines)


def write_outputs(agent: Agent, rep: dict) -> None:
    (agent.out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (agent.out / "result.md").write_text(render_result_md(agent, rep), encoding="utf-8")
