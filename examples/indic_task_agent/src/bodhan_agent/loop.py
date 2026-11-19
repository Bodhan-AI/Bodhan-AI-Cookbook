"""The agent loop: plan -> act -> verify -> (retry | re-plan) -> stop.

The LLM decides *what* to do; code decides what is *allowed* and what counts as
done:
  * paid tools are refused until a plan is recorded;
  * every tool call is one step; the last step only allows `finish`;
  * budget reservations happen inside the tools, so limits hold under parallel calls;
  * `finish` passes a verification gate: deliverables must be artifacts created
    in this run, translations/audio must carry a non-failing verification, and the
    final answer must be English;
  * forced stops (steps, time, budget, LLM outage) still write a truthful report.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bodhan_mcp import errors as E
from bodhan_mcp.config import BodhanKeys
from bodhan_mcp.documents import classify, read_text_file
from bodhan_mcp.registry import invoke
from bodhan_mcp.runtime import RunContext
from bodhan_mcp.tools import TOOL_BY_NAME, TOOLS
from bodhan_mcp.tools.common import ProvenanceGuard

from .llm import LLMError, PlannerLLM
from .prompts import SYSTEM_PROMPT, user_message
from .trace import CURRENT_STEP, TraceWriter, summarize_result

VERIFIED_KINDS = {"translation", "audio", "subtitles"}
FINISH_STATUSES = ["completed", "partial", "failed", "unsupported"]
MAX_TOOL_RESULT_CHARS = 7000
LLM_COMPLETION_RESERVE = 4000  # tokens assumed for a planner reply when checking a priced budget

CONTROL_TOOLS = [
    {"type": "function", "function": {
        "name": "record_plan",
        "description": "Record or revise the plan before acting. Required before any paid tool.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["steps"], "properties": {
            "steps": {"type": "array", "items": {"type": "string"}, "description": "Numbered steps you will take, each naming the tool."},
            "assumptions": {"type": "array", "items": {"type": "string"}, "description": "Assumptions made because the task was ambiguous."},
            "deliverables": {"type": "array", "items": {"type": "string"}, "description": "Outputs you intend to produce."}}}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "End the run. Deliverables must be artifact paths returned by tools in this run.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["status", "final_answer"], "properties": {
            "status": {"type": "string", "enum": FINISH_STATUSES, "description": "completed only if every requested output exists and passed verification."},
            "final_answer": {"type": "string", "description": "English answer/explanation for the user (no Indian-language text)."},
            "deliverables": {"type": "array", "items": {"type": "string"}, "description": "Artifact paths that answer the task."},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"}, "description": "Honest limitations, failures, skipped parts."}}}}},
]


def openai_tools(only: set[str] | None = None) -> list[dict[str, Any]]:
    specs = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema()}} for t in TOOLS]
    out = specs + CONTROL_TOOLS
    return [t for t in out if only is None or t["function"]["name"] in only]


@dataclass
class Settings:
    max_steps: int = 30
    max_cost_inr: float | None = 20.0
    max_seconds: float = 600.0
    llm_input_inr_per_1m: float | None = None
    llm_output_inr_per_1m: float | None = None


@dataclass
class Outcome:
    status: str
    stop_reason: str
    final_answer: str
    deliverables: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    planner_status: str | None = None


def expand_inputs(raw: list[str]) -> tuple[list[Path], list[str], list[Path]]:
    """Return (files, missing paths, roots allowed for reading)."""
    files, missing, roots = [], [], []
    for r in raw:
        for part in [x for x in r.split(",") if x.strip()] if "," in r and not Path(r).exists() else [r]:
            p = Path(part.strip()).expanduser()
            if not p.exists():
                missing.append(part)
                continue
            p = p.resolve()
            roots.append(p)
            if p.is_dir():
                files += sorted(x for x in p.rglob("*") if x.is_file() and not x.name.startswith("."))
            else:
                files.append(p)
    return files, missing, roots


class Agent:
    def __init__(self, task: str, ctx: RunContext, trace: TraceWriter, settings: Settings, llm: PlannerLLM | None,
                 files: list[Path], missing: list[str], llm_error: str | None = None):
        self.task, self.ctx, self.trace, self.settings, self.llm = task, ctx, trace, settings, llm
        self.files, self.missing, self.llm_error = files, missing, llm_error
        self.steps = 0
        self.plan: dict[str, Any] | None = None
        self.plan_version = 0
        self.outcome: Outcome | None = None
        self.finish_rejections = 0
        self.budget_errors = 0
        self.tool_log: list[dict[str, Any]] = []
        self.started = time.monotonic()
        self.inventory: dict[str, Any] = {}

    # ------------------------------------------------------------------ run
    async def run(self) -> Outcome:
        self.trace.write({"type": "run_start", "task": self.task, "files": [str(f) for f in self.files], "missing_inputs": self.missing,
                          "settings": self.settings.__dict__, "llm_model": getattr(self.llm, "model", None)})
        guard = ProvenanceGuard()
        guard.add(self.task)
        self.ctx.provenance_guard = guard
        await self._preflight(guard)

        if self.llm is None:
            return self._stop("failed", "llm_not_configured",
                              f"The planner LLM is not configured ({self.llm_error}). No task steps were attempted.")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message(self.task, self.inventory, self._budget_view(), BodhanKeys.from_env().availability(),
                                                     self.settings.max_steps)},
        ]
        nudges = 0
        while self.outcome is None:
            limit = self._limit_reached()
            if limit:
                return self._forced_stop(limit)
            last_step = self.steps >= self.settings.max_steps - 1
            tools = openai_tools({"finish"} if last_step else None)
            if last_step:
                messages.append({"role": "user", "content": "Step limit reached: call finish now with what you have (status partial if incomplete)."})
            est = self._llm_cost(len(json.dumps(messages, ensure_ascii=False)) // 3, LLM_COMPLETION_RESERVE)
            if est is not None and not self.ctx.ledger.can_afford(est):  # priced planner: check before spending
                return self._forced_stop(("budget_exhausted", f"Not enough budget left for another planner call (about ₹{est:.3f})."))
            try:
                reply = await self.llm.chat(messages, tools)
            except LLMError as exc:
                self.trace.write({"type": "llm_error", "step": self.steps, "error": str(exc)})
                return self._forced_stop(("llm_error", f"The planner LLM failed: {exc}"))
            cost = self._llm_cost(reply.prompt_tokens, reply.completion_tokens)
            self.ctx.ledger.add_llm(reply.prompt_tokens, reply.completion_tokens, cost)
            self.trace.write({"type": "llm_call", "step": self.steps, "latency_s": round(reply.latency_s, 2), "prompt_tokens": reply.prompt_tokens,
                              "completion_tokens": reply.completion_tokens, "cost_inr": cost, "finish_reason": reply.finish_reason,
                              "decision": [c.name for c in reply.tool_calls] or "no tool call", "rationale": reply.content[:600],
                              "fallback_text_tool_call": reply.fallback_parsed})
            messages.append(reply.assistant_message)
            if not reply.tool_calls:
                nudges += 1
                self.steps += 1
                if nudges > 2:
                    return self._forced_stop(("no_progress", "The planner stopped calling tools without finishing."))
                messages.append({"role": "user", "content": "Continue by calling a tool, or call finish(...) to end the run."})
                continue
            nudges = 0
            controls = [c for c in reply.tool_calls if c.name in ("record_plan", "finish")]
            work = [c for c in reply.tool_calls if c.name not in ("record_plan", "finish")]
            results: dict[str, dict] = {}
            for c in [x for x in controls if x.name == "record_plan"]:
                results[c.id] = self._record_plan(c)
            if work:
                outs = await asyncio.gather(*[self._run_tool(c, reply.content) for c in work])
                results.update({c.id: o for c, o in zip(work, outs)})
            for c in [x for x in controls if x.name == "finish"]:
                results[c.id] = self._finish(c, others_in_turn=bool(work))
            for c in reply.tool_calls:
                messages.append({"role": "tool", "tool_call_id": c.id, "content": _clip(results[c.id])})
            if self.budget_errors >= 3 and self.outcome is None:
                return self._forced_stop(("budget_exhausted", "Repeated budget errors; stopped to stay within --max-cost-inr."))
        return self.outcome

    # -------------------------------------------------------------- pieces
    async def _preflight(self, guard: ProvenanceGuard) -> None:
        t0 = time.monotonic()
        spec = TOOL_BY_NAME["inspect_files"]
        if self.files:
            result, _ = await invoke(spec, self.ctx, {"paths": [str(f) for f in self.files]})
            listed = result.get("files", [])
        else:
            listed = []
        for f in self.files:
            if classify(f) == "text":
                try:
                    guard.add(read_text_file(f))
                except E.ToolError:
                    pass
        self.inventory = {"files": listed, "missing_inputs": self.missing,
                          "note": "No input files were given." if not self.files and not self.missing else ""}
        self.trace.write({"type": "preflight", "step": 0, "tool": "inspect_files", "files": len(listed), "missing": self.missing,
                          "result_summary": [{k: f.get(k) for k in ("name", "kind", "duration_s", "pages", "chars")} for f in listed][:50],
                          "latency_s": round(time.monotonic() - t0, 3), "cost_inr": 0.0})

    def _record_plan(self, call) -> dict:
        self.steps += 1
        self.plan = {"steps": call.arguments.get("steps") or [], "assumptions": call.arguments.get("assumptions") or [],
                     "deliverables": call.arguments.get("deliverables") or []}
        self.plan_version += 1
        self.trace.write({"type": "plan", "step": self.steps, "version": self.plan_version, **self.plan})
        return {"ok": True, "plan_version": self.plan_version, "note": "Plan recorded. Execute it."}

    async def _run_tool(self, call, rationale: str) -> dict:
        self.steps += 1
        step = self.steps
        if step > self.settings.max_steps:
            return {"ok": False, "error": {"code": "step_limit", "message": "Step limit reached; call finish."}}
        spec = TOOL_BY_NAME.get(call.name)
        t0 = time.monotonic()
        if call.parse_error:
            result = {"ok": False, "error": {"code": E.INVALID_ARGUMENT, "message": call.parse_error, "hint": "Send arguments as a JSON object."}}
        elif spec is None:
            result = {"ok": False, "error": {"code": "unknown_tool", "message": f"No tool named {call.name!r}."}}
        elif spec.paid and self.plan is None:
            result = {"ok": False, "error": {"code": "plan_required", "message": "Call record_plan before using paid tools."}}
        else:
            CURRENT_STEP.set(step)
            result, _ = await invoke(spec, self.ctx, call.arguments)
        if not result.get("ok") and (result.get("error") or {}).get("code") == E.BUDGET_EXCEEDED:
            self.budget_errors += 1
        entry = {"type": "tool_call", "step": step, "plan_version": self.plan_version, "rationale": rationale[:400], "tool": call.name,
                 "arguments": call.arguments, "ok": bool(result.get("ok")), "result_summary": summarize_result(result),
                 "latency_s": round(time.monotonic() - t0, 3), "cost_inr": result.get("cost_inr", 0.0),
                 "budget_remaining_inr": result.get("budget_remaining_inr")}
        self.trace.write(entry)
        self.tool_log.append(entry)
        return result

    def _resolve_deliverable(self, raw: str):
        raw = str(raw).strip()
        arts = self.ctx.artifacts
        cand = Path(raw)
        for key in (str(cand), str(cand.resolve()) if cand.is_absolute() else "", str((self.ctx.artifacts_dir / cand.name).resolve()),
                    str((self.ctx.artifacts_dir.parent / cand).resolve())):
            if key and key in arts:
                return arts[key]
        return None

    def _finish(self, call, *, others_in_turn: bool) -> dict:
        self.steps += 1
        a = call.arguments
        status = a.get("status") if a.get("status") in FINISH_STATUSES else "partial"
        answer = str(a.get("final_answer") or "").strip()
        reasons: list[str] = []
        if others_in_turn:
            reasons.append("finish was called in the same turn as other tools; check their results first, then call finish alone")
        if not answer:
            reasons.append("final_answer is empty")
        indic_fields = [f for f, v in self._finish_texts(a) if not self._indic_ok(v)]
        if indic_fields:
            reasons.append(f"{', '.join(indic_fields)} contain Indian-language text you wrote; write English only and deliver Indic text as artifacts")
        delivered, unknown = [], []
        for d in a.get("deliverables") or []:
            art = self._resolve_deliverable(d)
            (delivered if art else unknown).append(art or d)
        if unknown:
            reasons.append(f"deliverables not produced by a tool in this run: {unknown}")
        unverified = [str(x.path.name) for x in self.ctx.artifacts.values() if x.kind in VERIFIED_KINDS and x.verification is None]
        if unverified:
            reasons.append(f"generated translations/audio without verification: {unverified}; verify them (verify_translation/verify_speech)")
        failing = [x for x in delivered if self._effective_status(x) == "fail"]
        if status == "completed" and failing:
            reasons.append("deliverables failed verification (directly or through embedded artifacts): "
                           + "; ".join(x.path.name for x in failing) + ". Repair them differently, or finish with status partial and explain.")
        if status == "completed" and not delivered:
            reasons.append("status completed needs at least one deliverable artifact made in this run (save a text answer with write_text)")
        if reasons and self.finish_rejections < 2:
            self.finish_rejections += 1
            self.trace.write({"type": "finish_rejected", "step": self.steps, "reasons": reasons})
            return {"ok": False, "error": {"code": "finish_rejected", "message": "Cannot finish yet: " + " | ".join(reasons)}}
        limitations = [str(x) for x in a.get("limitations") or []]
        assumptions = [str(x) for x in a.get("assumptions") or []]
        if indic_fields:  # never relaxed: replace planner-written Indic text with a code-written English note
            note = "[removed: the planner wrote Indian-language text here, which this agent does not allow]"
            answer = answer if self._indic_ok(answer) else ("The planner's final answer contained Indian-language text it wrote itself, so it was "
                                                            "withheld. See the deliverables below; only Bodhan-generated text is delivered.")
            limitations = [x if self._indic_ok(x) else note for x in limitations]
            assumptions = [x if self._indic_ok(x) else note for x in assumptions]
        if reasons:  # accepted after repeated rejection: downgrade honestly
            limitations += [f"verification gate: {r}" for r in reasons]
            if status == "completed":
                status = "partial" if delivered and not failing else "failed"
        final = status
        if status == "completed" and any(self._effective_status(x) == "warn" for x in delivered):
            final = "completed_with_warnings"
        self.outcome = Outcome(final, "finished", answer, [str(x.path) for x in delivered], assumptions, limitations, planner_status=a.get("status"))
        self.trace.write({"type": "finish", "step": self.steps, "status": final, "planner_status": a.get("status"),
                          "deliverables": self.outcome.deliverables, "gate_reasons": reasons})
        return {"ok": True, "status": final}

    @staticmethod
    def _finish_texts(a: dict) -> list[tuple[str, str]]:
        texts = [("final_answer", str(a.get("final_answer") or ""))]
        texts += [("assumptions", str(x)) for x in a.get("assumptions") or []]
        texts += [("limitations", str(x)) for x in a.get("limitations") or []]
        return texts

    def _indic_ok(self, text: str) -> bool:
        """Indic text in the planner's own words is allowed only when quoted verbatim from
        the task, an input or a Bodhan output (checked span by span)."""
        try:
            self.ctx.provenance_guard.check(text, "final answer")
            return True
        except E.ToolError:
            return False

    def _effective_status(self, art, seen: set[str] | None = None) -> str | None:
        """Worst verification status of an artifact and everything it embeds."""
        seen = seen or set()
        if str(art.path) in seen:
            return None
        seen.add(str(art.path))
        order = {None: 0, "pass": 1, "warn": 2, "fail": 3}
        worst = (art.verification or {}).get("status")
        if art.kind in VERIFIED_KINDS and art.verification is None:
            worst = "fail"
        for dep in art.depends_on:
            child = self.ctx.artifacts.get(dep)
            if child is not None:
                st = self._effective_status(child, seen)
                if order.get(st, 0) > order.get(worst, 0):
                    worst = st
        return worst

    # --------------------------------------------------------------- stops
    def _limit_reached(self) -> tuple[str, str] | None:
        if self.steps >= self.settings.max_steps + 1:
            return ("step_limit", f"Reached --max-steps {self.settings.max_steps}.")
        if time.monotonic() - self.started > self.settings.max_seconds:
            return ("time_limit", f"Reached the {self.settings.max_seconds:.0f}s time limit.")
        rem = self.ctx.ledger.remaining
        if rem is not None and rem <= 0.0005:
            return ("budget_exhausted", "The --max-cost-inr budget is used up.")
        return None

    def _forced_stop(self, limit: tuple[str, str]) -> Outcome:
        reason, text = limit
        produced = [x for x in self.ctx.artifacts.values() if x.kind in VERIFIED_KINDS | {"text", "ocr", "transcript"}]
        good = [x for x in produced if (x.verification or {}).get("status") != "fail"]
        status = "partial" if good else "failed"
        answer = (f"The run stopped before the planner finished: {text} "
                  + (f"{len(good)} artifact(s) were produced and are listed below; they may not fully answer the task."
                     if good else "No usable output was produced."))
        return self._stop(status, reason, answer, [str(x.path) for x in good],
                          [f"Stopped early ({reason}); outputs were not confirmed complete by the planner."])

    def _stop(self, status: str, reason: str, answer: str, deliverables: list[str] | None = None, limitations: list[str] | None = None) -> Outcome:
        self.outcome = Outcome(status, reason, answer, deliverables or [], (self.plan or {}).get("assumptions", []), limitations or [])
        self.trace.write({"type": "stop", "step": self.steps, "status": status, "reason": reason, "message": answer})
        return self.outcome

    # ----------------------------------------------------------------- misc
    def _budget_view(self) -> dict:
        return {"max_cost_inr": self.settings.max_cost_inr, "prices": {"indic-transcribe": "₹0.10/min", "indic-translate": "₹0.20/10k output tokens",
                "indic-speak": "₹6/10k chars", "indic-ocr": "₹0.20/page"}}

    def _llm_cost(self, prompt: int, completion: int) -> float | None:
        s = self.settings
        if s.llm_input_inr_per_1m is None and s.llm_output_inr_per_1m is None:
            return None
        return prompt / 1e6 * (s.llm_input_inr_per_1m or 0) + completion / 1e6 * (s.llm_output_inr_per_1m or 0)


def _clip(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return text[:MAX_TOOL_RESULT_CHARS] + '... [truncated; full content is in the artifact files - use read_text]"'
