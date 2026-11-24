"""Run orchestration: preflight → plan → feasibility → act loop → verify (→ one repair) → outputs.

Every exit path writes the full output layout (result.md, artifacts/, trace.jsonl, report.json).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from bodhan_core.config import load_settings

from .llm import LLM, LLMError
from .loop import ActLoop, Budget, LoopOutcome
from .planner import Plan, check_feasibility, make_plan
from .preflight import build_manifest
from .report import copy_artifacts, write_report_json, write_result_md
from .session import ToolSession
from .trace import Trace
from .verify import Verification, verify


@dataclass
class RunConfig:
    task: str
    files: list[Path]
    out: Path
    max_steps: int = 30
    max_cost_inr: float = 20.0
    call_timeout_s: float = 300.0
    max_minutes: float = 15.0


@dataclass
class _State:
    status: str = "failed"
    answer: str = ""
    reason: str = ""
    plan: Plan | None = None
    outcome: LoopOutcome | None = None
    verification: Verification = field(default_factory=Verification)
    assumptions: list[str] = field(default_factory=list)
    manifest: list[dict] = field(default_factory=list)
    llm_usage: dict = field(default_factory=dict)
    repaired: bool = False
    cost_inr: float = 0.0
    steps: int = 0


def run_task(cfg: RunConfig) -> dict:
    return asyncio.run(_run(cfg))


async def _run(cfg: RunConfig) -> dict:
    started = time.monotonic()
    out = cfg.out
    (out / "artifacts").mkdir(parents=True, exist_ok=True)
    work = out / ".work"
    work.mkdir(parents=True, exist_ok=True)
    trace = Trace(out / "trace.jsonl")
    trace.log("start", task=cfg.task, files=[str(f) for f in cfg.files],
              budgets={"max_steps": cfg.max_steps, "max_cost_inr": cfg.max_cost_inr,
                       "call_timeout_s": cfg.call_timeout_s, "max_minutes": cfg.max_minutes})
    st = _State()
    try:
        await _pipeline(cfg, trace, st, work, started)
    except LLMError as e:
        st.status, st.reason = "failed", f"The LLM could not be used: {e}"
        trace.log("error", error=st.reason)
    except Exception as e:  # noqa: BLE001 - always write outputs
        st.status, st.reason = "failed", f"Unexpected {type(e).__name__}: {e}"
        trace.log("error", error=st.reason)
    return _finalize(cfg, trace, st, work, started)


async def _pipeline(cfg: RunConfig, trace: Trace, st: _State, work: Path, started: float) -> None:
    settings = load_settings()
    async with ToolSession(work, max_cost_inr=cfg.max_cost_inr, call_timeout_s=cfg.call_timeout_s) as session:
        manifest = await build_manifest(session, cfg.files)
        st.manifest = manifest.files
        trace.log("preflight", manifest=manifest.files, missing=manifest.missing, unreadable=manifest.unreadable)
        if not manifest.files:
            st.status = "impossible"
            details = manifest.missing or [u["name"] for u in manifest.unreadable] or ["(none given)"]
            st.reason = ("No usable input files: " + ", ".join(details) + ". The task cannot be done without its "
                         "inputs, so nothing was attempted.")
            return

        llm = LLM(settings, timeout_s=min(cfg.call_timeout_s, 180))
        plan = await make_plan(llm, cfg.task, manifest.files, manifest.missing, trace)
        st.plan = plan
        st.assumptions += plan.assumptions
        status, reason, notes = check_feasibility(cfg.task, plan, manifest.files, manifest.missing, cfg.max_cost_inr)
        trace.log("feasibility", feasible=status is None, status=status, reason=reason, notes=notes)
        st.assumptions += [n for n in notes if not n.startswith("Estimated")]
        if status:
            st.status, st.reason = status, reason
            st.llm_usage = llm.usage
            return

        budget = Budget(cfg.max_steps, cfg.max_cost_inr, deadline=started + cfg.max_minutes * 60, steps=1)
        loop = ActLoop(llm, session, trace, budget, work)
        loop.start(cfg.task, manifest.files, plan.model_dump(), manifest.missing)
        outcome = await loop.run()
        st.outcome = outcome
        if outcome.status == "finished":
            st.verification = verify(plan, outcome.answer, outcome.artifacts, outcome.results, work)
            trace.log("verify", checks=st.verification.checks, missed_targets=st.verification.missed_targets)
            if st.verification.failures and not budget.exhausted():
                st.repaired = True
                failed = "; ".join(f"{c['check']}: {c['detail']}" for c in st.verification.failures)
                trace.log("repair", failures=failed)
                loop.add_user(f"Verification failed: {failed}. Fix these problems (re-run the needed tools), then "
                              f"call finish again with the corrected artifacts. If they cannot be fixed, call "
                              f"finish with what is correct and explain what is missing.")
                outcome = await loop.run()
                st.outcome = outcome
                if outcome.status == "finished":
                    st.verification = verify(plan, outcome.answer, outcome.artifacts, outcome.results, work)
                    trace.log("verify", checks=st.verification.checks, missed_targets=st.verification.missed_targets,
                              after_repair=True)
        st.answer = outcome.answer
        if outcome.assumptions:
            # Made after seeing the content: these replace the plan's up-front guesses.
            st.assumptions = [a for a in st.assumptions if a not in plan.assumptions] + outcome.assumptions
        st.cost_inr, st.steps = budget.cost_inr, budget.steps
        st.llm_usage = llm.usage
        if outcome.status == "finished":
            st.status = "partial" if st.verification.failures else "completed"
            if st.verification.failures:
                st.reason = "Some checks failed: " + "; ".join(c["detail"] for c in st.verification.failures)
        elif outcome.status == "gave_up":
            st.status, st.reason = ("partial" if outcome.artifacts else "impossible"), outcome.reason
        elif outcome.status == "budget_exhausted":
            st.status, st.reason = "budget_exhausted", f"Stopped cleanly: {outcome.reason}."
        else:
            st.status, st.reason = "failed", outcome.reason


def _finalize(cfg: RunConfig, trace: Trace, st: _State, work: Path, started: float) -> dict:
    outcome = st.outcome
    artifacts = []
    if outcome and st.status in {"completed", "partial"}:
        # Artifacts that failed a check are not presented as results.
        failed = {c["artifact"] for c in st.verification.checks if c.get("artifact") and not c["passed"]}
        keep = [a for a in outcome.artifacts if a not in failed and Path(a).name not in failed]
        artifacts = copy_artifacts(keep, cfg.out / "artifacts")
    produced = sorted({Path(p).name for p in (outcome.results if outcome else {})})
    seconds = round(time.monotonic() - started, 2)
    assumptions = list(dict.fromkeys(st.assumptions))
    trace.log("end", status=st.status, cost_inr=round(st.cost_inr, 4), seconds=seconds)
    report = {
        "status": st.status,
        "task": cfg.task,
        "reason": st.reason or None,
        "answer": st.answer or None,
        "steps": st.steps,
        "trace_entries": trace.step,
        "tool_calls": outcome.calls if outcome else [],
        "plan": st.plan.model_dump() if st.plan else None,
        "inputs": st.manifest,
        "artifacts": [str(p.relative_to(cfg.out)) for p in artifacts],
        "verification": st.verification.checks,
        "verification_passed": bool(st.verification.checks) and not st.verification.failures,
        "repair_round_used": st.repaired,
        "missed_targets": st.verification.missed_targets,
        "assumptions": assumptions,
        "total_cost_inr": round(st.cost_inr, 4),
        "budget": {"max_steps": cfg.max_steps, "max_cost_inr": cfg.max_cost_inr, "max_minutes": cfg.max_minutes},
        "total_time_s": seconds,
        "llm": st.llm_usage,
    }
    write_report_json(cfg.out, report)
    write_result_md(cfg.out, task=cfg.task, status=st.status, answer=st.answer, reason=st.reason, artifacts=artifacts,
                    assumptions=assumptions, verification=st.verification.checks,
                    missed=st.verification.missed_targets, produced=produced, cost_inr=st.cost_inr, seconds=seconds)
    return report
