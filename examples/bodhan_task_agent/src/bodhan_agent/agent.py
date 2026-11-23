"""The task agent loop: preflight → plan → act → verify → finish, under step, cost and time budgets.

Division of labour (see docs/design_notes.md):

* Plain code: input discovery and inspection, language-name and script detection, budget
  accounting and enforcement, guards on what the LLM may write, duplicate-call suppression,
  copying artifacts, final verification, the status decision and the report.
* LLM: understanding the task, judging feasibility, choosing tools and their order, choosing
  languages/voices when the task is ambiguous, writing English summaries/explanations,
  reacting to verification failures, and the final English answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bodhan_mcp import languages

from . import prompts, report, safety, verify
from .budget import PAID_TOOLS, Budget
from .llm import LLM, LLMError, LLMResponse
from .toolbox import PLAN_TOOL, Toolbox, ToolOutcome
from .trace import Trace

log = logging.getLogger("bodhan_agent")

MAX_PARALLEL_CALLS = 8
MAX_NUDGES = 2


@dataclass
class RunState:
    task: str
    files: list[str]
    out_dir: Path
    plan: dict[str, Any] | None = None
    finish: dict[str, Any] | None = None
    stop_reason: str | None = None
    assumptions: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    injection: list[dict[str, Any]] = field(default_factory=list)
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: int = 0
    llm_tokens: dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})
    budget_hit: bool = False
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] | None = None
    preflight: dict[str, Any] = field(default_factory=dict)


def _expand_inputs(files: list[str]) -> tuple[list[Path], list[str], list[Path]]:
    """Existing input files (directories expanded), missing paths, and the roots the tools may read."""
    found: list[Path] = []
    missing: list[str] = []
    roots: list[Path] = []
    for raw in files:
        p = Path(raw).expanduser()
        if not p.exists():
            missing.append(raw)
            continue
        p = p.resolve()
        if p.is_dir():
            roots.append(p)
            found.extend(sorted(c.resolve() for c in p.rglob("*") if c.is_file() and not c.name.startswith(".")))
        else:
            roots.append(p)
            found.append(p)
    return found[:200], missing, roots


class Agent:
    def __init__(self, llm: LLM, *, budget: Budget, server_mode: str = "inproc", server: Any = None) -> None:
        self.llm = llm
        self.budget = budget
        self.server_mode = server_mode
        self.server = server

    async def run(self, task: str, files: list[str], out_dir: Path) -> RunState:
        out_dir = out_dir.resolve()
        (out_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        work = out_dir / "work"
        state = RunState(task=task, files=files, out_dir=out_dir)
        trace = Trace(out_dir / "trace.jsonl")
        trace.write("run_start", 0, task=task, files=files, out_dir=str(out_dir), budget=self.budget.snapshot(),
                    llm_model=getattr(self.llm, "model", "?"), server_mode=self.server_mode)
        found, missing, roots = _expand_inputs(files)
        try:
            async with Toolbox(work_dir=work, read_roots=roots, budget=self.budget, server_mode=self.server_mode,
                               server=self.server) as tb:
                await self._preflight(state, tb, trace, found, missing)
                await self._loop(state, tb, trace)
                self._finalise(state, tb, trace)
        except Exception as exc:  # last resort: still write an honest report
            log.exception("agent crashed")
            state.errors.append({"code": "agent_crash", "message": f"{type(exc).__name__}: {exc}"})
            state.stop_reason = state.stop_reason or "agent_error"
            trace.write("error", self.budget.steps, code="agent_crash", message=str(exc))
        status = report.decide_status(state)
        report.write_outputs(state, self.budget, status, trace)
        trace.write("run_end", self.budget.steps, status=status, cost_inr=round(self.budget.spent_inr, 4),
                    elapsed_s=round(self.budget.elapsed_s, 2))
        trace.close()
        return state

    # ---------------------------------------------------------------------------------
    async def _preflight(self, state: RunState, tb: Toolbox, trace: Trace, found: list[Path], missing: list[str]) -> None:
        files_info: list[dict[str, Any]] = []
        if found:
            out = await tb.call("inspect_media", {"paths": [str(p) for p in found]})
            if out.ok and out.data:
                for f in out.data.get("files", []):
                    files_info.append({k: v for k, v in f.items() if v not in (None, [], "")})
                if out.injection:
                    state.injection.append({"where": "input file previews", "excerpts": out.injection})
        mentions = languages.mentioned_languages(state.task)
        state.preflight = {"files": files_info, "missing": missing,
                           "task_languages": mentions if (mentions["supported"] or mentions["unsupported"]) else None,
                           "injection": [e for i in state.injection for e in i["excerpts"]]}
        trace.write("preflight", 0, files=[{k: f.get(k) for k in ("name", "kind", "duration_s", "pages", "chars",
                                                                   "detected_script", "error")} for f in files_info],
                    missing=missing, task_languages=mentions, injection=state.preflight["injection"])

    # ---------------------------------------------------------------------------------
    async def _ask(self, state: RunState, trace: Trace, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                   tool_choice: Any = None) -> LLMResponse | None:
        try:
            resp = await self.llm.chat(messages, tools, tool_choice=tool_choice,
                                       timeout=max(5.0, min(120.0, self.budget.remaining_s)))
        except LLMError as exc:
            state.errors.append({"code": "llm_error", "message": str(exc)[:500]})
            trace.write("llm_error", self.budget.steps, message=str(exc)[:1000])
            state.stop_reason = "llm_error"
            return None
        state.llm_calls += 1
        for k in state.llm_tokens:
            state.llm_tokens[k] += int(resp.usage.get(k, 0))
        trace.write("llm", self.budget.steps, response=resp.to_dict())
        return resp

    async def _loop(self, state: RunState, tb: Toolbox, trace: Trace) -> None:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "user", "content": prompts.context_message(state.task, state.preflight, self.budget.snapshot())},
        ]
        tools = [PLAN_TOOL, *tb.tool_specs]
        nudges = 0
        finish_rejections = 0
        while True:
            if self.budget.steps >= self.budget.max_steps:
                state.stop_reason = "max_steps"
                trace.write("stop", self.budget.steps, reason="max_steps")
                return
            if self.budget.remaining_s <= 1:
                state.stop_reason = "max_time"
                trace.write("stop", self.budget.steps, reason="max_time")
                return
            self.budget.steps += 1
            step = self.budget.steps
            if state.plan is None:
                choice: Any = {"type": "function", "function": {"name": "submit_plan"}}
            else:
                choice = None
                if self.budget.steps_left <= 1:
                    messages.append({"role": "user", "content": "Step budget almost exhausted: call finish NOW with "
                                     "an honest status, listing only what was actually produced."})
            resp = await self._ask(state, trace, messages, tools, choice)
            if resp is None:
                return
            # Every tool call in the assistant message must get a tool response, so cap before recording.
            resp.tool_calls = resp.tool_calls[:MAX_PARALLEL_CALLS]
            messages.append(resp.to_message())
            if not resp.tool_calls:
                nudges += 1
                if nudges > MAX_NUDGES:
                    state.stop_reason = "no_action"
                    state.errors.append({"code": "no_action", "message": "LLM stopped calling tools without finishing."})
                    trace.write("stop", step, reason="no_action")
                    return
                messages.append({"role": "user", "content": "You must act through tools. "
                                 + ("Call submit_plan first." if state.plan is None else
                                    "Call the next tool, or call finish to end the task.")})
                continue
            nudges = 0

            calls = resp.tool_calls[:MAX_PARALLEL_CALLS]
            parsed: list[tuple[Any, dict[str, Any] | None, str | None]] = []
            for tc in calls:
                try:
                    args = json.loads(tc.arguments or "{}")
                    parsed.append((tc, args if isinstance(args, dict) else None, None if isinstance(args, dict) else "arguments must be a JSON object"))
                except json.JSONDecodeError as exc:
                    parsed.append((tc, None, f"invalid JSON arguments: {exc}"))

            # Plan / finish are control calls, handled first and alone.
            finish_call = next((p for p in parsed if p[0].name == "finish"), None)
            plan_call = next((p for p in parsed if p[0].name == "submit_plan"), None)
            results: dict[str, str] = {}
            if plan_call:
                results[plan_call[0].id] = self._handle_plan(state, trace, step, plan_call[1] or {})
                if state.plan is not None and state.plan.get("feasible") is False:
                    self._respond(messages, parsed, results, skip_msg="Plan marked infeasible; finishing.")
                    state.finish = {"status": "infeasible", "answer": state.plan.get("infeasible_reason") or
                                    state.plan.get("understanding", ""), "artifacts": [],
                                    "assumptions": state.plan.get("assumptions") or []}
                    self._check_english_answer(state)
                    trace.write("finish", step, **state.finish, source="plan")
                    return
            elif state.plan is None:
                # The model skipped planning: accept its first action but record an implicit plan.
                state.plan = {"understanding": "(no explicit plan submitted)", "feasible": True, "steps": []}
                trace.write("plan", step, plan=state.plan, implicit=True)

            actions = [p for p in parsed if p[0].name not in {"finish", "submit_plan"}]
            if actions:
                await self._run_actions(state, tb, trace, step, actions, results, thought=resp.content)

            if finish_call and not actions:
                verdict = self._handle_finish(state, tb, trace, step, finish_call[1] or {}, finish_rejections)
                if verdict is None:
                    return
                finish_rejections += 1
                results[finish_call[0].id] = verdict
            elif finish_call:
                results[finish_call[0].id] = json.dumps({"ok": False, "error": {
                    "code": "finish_with_actions", "message": "finish must be called alone, after seeing the results "
                                                              "of the other calls. Call it again next turn."}})
            self._respond(messages, parsed, results)

    def _respond(self, messages: list[dict[str, Any]], parsed: list, results: dict[str, str], skip_msg: str = "") -> None:
        for tc, _, _ in parsed:
            messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                             "content": results.get(tc.id) or json.dumps({"ok": False, "error": {
                                 "code": "skipped", "message": skip_msg or "not executed"}})})

    def _handle_plan(self, state: RunState, trace: Trace, step: int, args: dict[str, Any]) -> str:
        args.setdefault("feasible", True)
        for key in ("understanding", "infeasible_reason"):
            if isinstance(args.get(key), str) and safety.indic_char_count(args[key]):
                args[key] = "(non-English text removed by guard) " + "".join(
                    ch for ch in args[key] if not safety.indic_char_count(ch))
        state.plan = args
        state.assumptions.extend(str(a) for a in args.get("assumptions") or [])
        trace.write("plan", step, plan=args)
        return json.dumps({"ok": True, "result": "Plan recorded. Execute it now; independent calls can go in one turn."})

    async def _run_actions(self, state: RunState, tb: Toolbox, trace: Trace, step: int, actions: list,
                           results: dict[str, str], thought: str) -> None:
        n_paid = sum(1 for tc, a, _ in actions if a is not None and tc.name in PAID_TOOLS)
        share = self.budget.share_for(n_paid)

        async def one(tc: Any, args: dict[str, Any] | None, err: str | None) -> tuple[Any, ToolOutcome]:
            if args is None:
                return tc, ToolOutcome(tc.name, {}, False, error={"code": "invalid_arguments", "message": err})
            if tc.name in PAID_TOOLS and self.budget.remaining_inr <= 1e-6:
                return tc, ToolOutcome(tc.name, args, False, error={
                    "code": "budget_exceeded", "message": "The run's rupee budget is exhausted.",
                    "hint": "Finish now and report what was completed."})
            return tc, await tb.call(tc.name, args, max_cost_inr=share if tc.name in PAID_TOOLS else None)

        outcomes = await asyncio.gather(*(one(*a) for a in actions))
        for tc, out in outcomes:
            self.budget.charge(tc.name, out.cost_inr, out.calls)
            if not out.ok:
                code = (out.error or {}).get("code")
                if code == "budget_exceeded":
                    state.budget_hit = True
                state.errors.append({"step": step, "tool": tc.name, **{k: (out.error or {}).get(k)
                                                                        for k in ("code", "message")}})
            if out.injection:
                state.injection.append({"where": f"{tc.name} result (step {step})", "excerpts": out.injection})
            entry = {
                "step": step, "plan": thought[:500] if thought else None, "tool": tc.name, "arguments": out.arguments,
                "ok": out.ok, "result_summary": out.summary(), "latency_ms": out.latency_ms,
                "cost_inr": round(out.cost_inr, 6), "cumulative_cost_inr": round(self.budget.spent_inr, 6),
                "memoised": out.memoised, "model_calls": len(out.calls),
                "cache_hits": sum(1 for c in out.calls if c.get("cached")),
            }
            if not out.ok:
                entry["error"] = out.error
            if out.ok and isinstance((out.data or {}).get("verification"), dict):
                entry["verification_passed"] = out.data["verification"].get("passed")
            state.tool_log.append(entry)
            trace.write("tool", step, **{k: v for k, v in entry.items() if k != "step"})
            results[tc.id] = tb.message_for_llm(out)

    def _check_english_answer(self, state: RunState) -> None:
        f = state.finish or {}
        if safety.indic_char_count(f.get("answer", "")):
            f["answer"] = "".join(ch if not safety.indic_char_count(ch) else "" for ch in f["answer"]).strip() + \
                          " [Indian-language text removed from the agent's own answer; see the artifacts.]"

    def _handle_finish(self, state: RunState, tb: Toolbox, trace: Trace, step: int, args: dict[str, Any],
                       rejections: int) -> str | None:
        """Validate a finish call. Returns None if accepted, or feedback for the LLM if rejected once."""
        status = args.get("status") if args.get("status") in {"completed", "partial", "failed", "infeasible"} else "partial"
        answer = str(args.get("answer") or "").strip()
        problems: list[str] = []
        if safety.indic_char_count(answer):
            problems.append("The answer contains Indian-script text; write it in English and point to the artifact files.")
        deliverables: list[tuple[Path, str]] = []
        for a in args.get("artifacts") or []:
            if not isinstance(a, dict) or not a.get("path"):
                continue
            p = Path(str(a["path"]).replace("file://", "", 1)).expanduser()
            p = (p if p.is_absolute() else Path.cwd() / p).resolve()
            if not p.is_file():
                problems.append(f"Artifact {a['path']} does not exist.")
            elif not safety.is_within(p, [tb.work_dir]):
                problems.append(f"Artifact {a['path']} was not produced in this run.")
            else:
                deliverables.append((p, str(a.get("description") or "")))
        # Spoken audio always ships with the text it speaks (when that text was made in this run),
        # so the listener can read it and a reviewer can check it against the audio.
        for p, _desc in list(deliverables):
            info = tb.produced.get(str(p), {})
            src = (info.get("args") or {}).get("text_file") if info.get("tool") == "synthesize_speech" else None
            if src:
                sp = Path(str(src)).expanduser().resolve()
                if sp.is_file() and safety.is_within(sp, [tb.work_dir]) and all(sp != q for q, _ in deliverables):
                    deliverables.append((sp, f"Text spoken in {p.name}"))
        if status == "completed" and not deliverables and not answer:
            problems.append("status=completed but there is neither an answer nor any artifact.")
        ver = verify.verify_run(state.task, tb.produced, [p for p, _ in deliverables], tb.authored)
        if status == "completed" and not ver["passed"]:
            failed = [f"{c.get('file', '')} {c['name']}: {c['detail']}" for c in ver["checks"]
                      if not c["passed"] and c["severity"] == "error"]
            problems.append("Verification failed: " + "; ".join(failed)[:800])
        trace.write("verification", step, passed=ver["passed"], checks=ver["checks"], problems=problems)
        can_retry = rejections == 0 and self.budget.steps_left >= 2 and self.budget.remaining_s > 20
        if problems and can_retry:
            return json.dumps({"ok": False, "error": {"code": "finish_rejected", "message": " ".join(problems),
                                                      "hint": "Fix the problem (e.g. retry the failed step once), or "
                                                              "call finish again with status 'partial' and an honest "
                                                              "explanation."}}, ensure_ascii=False)
        if problems and status == "completed":
            status = "partial"  # an unresolved problem can never end as completed
        state.finish = {"status": status, "answer": answer, "assumptions": [str(x) for x in args.get("assumptions") or []],
                        "claimed_status": args.get("status")}
        self._check_english_answer(state)
        state.assumptions.extend(a for a in state.finish["assumptions"] if a not in state.assumptions)
        state.verification = ver
        if problems:
            state.errors.append({"step": step, "code": "finish_problems", "message": " ".join(problems)[:800]})
        # Copy deliverables into artifacts/ with stable, unique names.
        art_dir = state.out_dir / "artifacts"
        used: set[str] = set()
        for p, desc in deliverables:
            name = p.name
            i = 2
            while name in used:
                name = f"{p.stem}-{i}{p.suffix}"
                i += 1
            used.add(name)
            shutil.copy2(p, art_dir / name)
            info = tb.produced.get(str(p), {})
            state.artifacts.append({"file": f"artifacts/{name}", "description": desc, "produced_by": info.get("tool"),
                                    "bytes": p.stat().st_size,
                                    "ai_generated_audio": info.get("tool") == "synthesize_speech"})
        trace.write("finish", step, status=status, answer=state.finish["answer"], artifacts=state.artifacts,
                    assumptions=state.finish["assumptions"])
        return None

    def _finalise(self, state: RunState, tb: Toolbox, trace: Trace) -> None:
        if state.verification is None:
            # Run stopped before an accepted finish: still verify everything produced so far.
            state.verification = verify.verify_run(state.task, tb.produced, [], tb.authored)
            trace.write("verification", self.budget.steps, passed=state.verification["passed"],
                        checks=state.verification["checks"], note="run ended without an accepted finish")
        state.preflight["produced_files"] = sorted(str(Path(p).relative_to(state.out_dir)) for p in tb.produced
                                                   if Path(p).is_relative_to(state.out_dir))
