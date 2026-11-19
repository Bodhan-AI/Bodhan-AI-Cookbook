"""The agent loop: plan -> act -> verify -> (retry | re-plan) -> stop.

The LLM decides *what* to do; code decides what is *allowed* and what counts as done:
  * control calls (record_plan, finish) are typed and validated like tools; a malformed
    call is an error the planner can fix, never a crash or an empty plan;
  * the plan declares deliverables (kind + language); `completed` requires each one to be
    an existing artifact made in this run whose verification (including everything it was
    made from) did not fail. An answer-only task declares kind "answer";
  * paid tools are refused until a plan is recorded;
  * every call the planner makes is one step (controls included); a turn may not use more
    steps than remain, and the last step only allows `finish`;
  * budget reservations happen inside the tools, so limits hold under parallel calls;
  * one deadline (from CLI start) bounds preflight, planner calls, tools and media work;
  * the planner may not write Indian-language text anywhere (plan, answer, notes);
  * every stop path (finish, limits, outage, crash) uses the same rule for what may be
    presented as a usable output;
  * the run is checkpointed between turns so it can be resumed (see state.py).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bodhan_mcp import errors as E
from bodhan_mcp.config import BodhanKeys
from bodhan_mcp.documents import classify, read_text_file
from bodhan_mcp.languages import LANGUAGE_NAMES, normalize_language
from bodhan_mcp.registry import clean_schema, invoke
from bodhan_mcp.runtime import RUN_DEADLINE, Artifact, RunContext
from bodhan_mcp.tools import TOOL_BY_NAME, TOOLS
from bodhan_mcp.tools.common import ProvenanceGuard

from . import state as checkpoint
from .llm import LLMError, PlannerLLM, ToolCall
from .prompts import SYSTEM_PROMPT, user_message
from .trace import CURRENT_STEP, TraceWriter, summarize_result

PAID_TOOLS = {t.name for t in TOOLS if t.paid}
VERIFIED_KINDS = {"translation", "audio", "subtitles"}
OUTPUT_KINDS = VERIFIED_KINDS | {"text", "ocr", "transcript"}
FINISH_STATUSES = ["completed", "partial", "failed", "unsupported"]
MAX_TOOL_RESULT_CHARS = 7000
LLM_COMPLETION_RESERVE = 4000  # completion tokens allowed per planner call when its price is known
# Upper bound on a priced planner call's prompt tokens: byte-level BPE and byte-fallback tokenizers never make a token
# smaller than one UTF-8 byte, so tokens <= bytes. Margins cover per-message special tokens and the provider's own
# formatting of the tool list. (A character count is not a bound: one Indic character is 3 bytes and may be 3 tokens.)
PROMPT_TOKENS_PER_MESSAGE = 16
PROMPT_TOKENS_MARGIN = 1000


def prompt_token_bound(messages: list[dict], tools: list[dict]) -> int:
    body = json.dumps(messages, ensure_ascii=False).encode("utf-8") + json.dumps(tools, ensure_ascii=False).encode("utf-8")
    return len(body) + PROMPT_TOKENS_PER_MESSAGE * len(messages) + PROMPT_TOKENS_MARGIN

# Which artifact kinds satisfy a declared deliverable kind.
KIND_MATCH = {"audio": {"audio"}, "translation": {"translation", "subtitles"}, "subtitles": {"subtitles"},
              "text": {"text", "translation", "ocr", "transcript"}, "transcript": {"transcript", "subtitles"}}


# ---------------------------------------------------------------------------
# Typed control calls
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliverableSpec(_Strict):
    kind: Literal["audio", "translation", "subtitles", "text", "transcript", "answer"] = Field(
        description="audio, translation, subtitles, text (a written summary/report/list), transcript, or answer (no file: the reply itself)")
    language: str | None = Field(None, max_length=12, description="Language code of the output, e.g. 'ta' (omit if not language-specific).")
    source: str | None = Field(None, max_length=200, description="Input file name this output is made from, when the task asks for one output per file.")
    optional: bool = Field(False, description="True for conditional outputs that may legitimately not exist (e.g. 'translate the Telugu clips' when none is Telugu).")
    description: str = Field("", max_length=300, description="What this output is, e.g. 'two-minute Tamil summary audio'.")


class RecordPlanIn(_Strict):
    steps: list[str] = Field(min_length=1, max_length=40, description="Numbered steps you will take, each naming the tool.")
    deliverables: list[DeliverableSpec] = Field(min_length=1, max_length=12,
                                                description="Every output the task asks for. Use kind 'answer' when the reply itself is the output.")
    assumptions: list[str] = Field(default_factory=list, max_length=20, description="Assumptions made because the task was ambiguous.")


class FinishIn(_Strict):
    status: Literal["completed", "partial", "failed", "unsupported"] = Field(
        description="completed only if every declared deliverable exists and passed verification.")
    final_answer: str = Field(min_length=1, max_length=6000, description="English answer/explanation for the user (no Indian-language text).")
    deliverables: list[str] = Field(default_factory=list, max_length=30, description="Artifact paths that answer the task.")
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=30, description="Honest limitations, failures, skipped parts.")


CONTROL_SPECS = {
    "record_plan": ("Record or revise the plan before acting: steps and the deliverables the task asks for. Required before any paid tool.",
                    RecordPlanIn),
    "finish": ("End the run. Deliverables must be artifact paths returned by tools in this run.", FinishIn),
}
CONTROL_TOOLS = [{"type": "function", "function": {"name": n, "description": d, "parameters": clean_schema(m.model_json_schema())}}
                 for n, (d, m) in CONTROL_SPECS.items()]


def openai_tools(only: set[str] | None = None) -> list[dict[str, Any]]:
    specs = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema()}} for t in TOOLS]
    out = specs + CONTROL_TOOLS
    return [t for t in out if only is None or t["function"]["name"] in only]


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ())) or '(arguments)'}: {e.get('msg')}" for e in exc.errors()[:8])


# ---------------------------------------------------------------------------
# Settings, outcome, inputs
# ---------------------------------------------------------------------------

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


def expand_inputs(raw: list[str], exclude: Path | None = None) -> tuple[list[Path], list[str], list[Path]]:
    """Return (files, missing paths, roots allowed for reading). Files under `exclude`
    (the run's own output folder) are never treated as inputs, and hidden folders such as
    .state/.previous_runs are skipped when listing a directory."""
    files, missing, roots = [], [], []
    ex = exclude.resolve() if exclude else None
    for r in raw:
        for part in [x for x in r.split(",") if x.strip()] if "," in r and not Path(r).exists() else [r]:
            p = Path(part.strip()).expanduser()
            if not p.exists():
                missing.append(part)
                continue
            p = p.resolve()
            roots.append(p)
            if p.is_dir():
                for x in sorted(p.rglob("*")):
                    rel = x.relative_to(p).parts
                    if not x.is_file() or any(part.startswith(".") for part in rel):
                        continue
                    if ex is not None and (x.resolve() == ex or x.resolve().is_relative_to(ex)):
                        continue
                    files.append(x.resolve())
            else:
                files.append(p)
    return files, missing, roots


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    def __init__(self, task: str, ctx: RunContext, trace: TraceWriter, settings: Settings, llm: PlannerLLM | None,
                 files: list[Path], missing: list[str], llm_error: str | None = None, *, deadline: float | None = None,
                 out: Path | None = None, resume_state: dict[str, Any] | None = None, notes: list[str] | None = None):
        self.task, self.ctx, self.trace, self.settings, self.llm = task, ctx, trace, settings, llm
        self.files, self.missing, self.llm_error = files, missing, llm_error
        self.out = out
        self.deadline = deadline if deadline is not None else time.monotonic() + settings.max_seconds
        self.steps = 0
        self.plan: dict[str, Any] | None = None
        self.plan_version = 0
        self.outcome: Outcome | None = None
        self.finish_rejections = 0
        self.budget_errors = 0
        self.tool_log: list[dict[str, Any]] = []
        self.inventory: dict[str, Any] = {}
        self.messages: list[dict[str, Any]] = []
        self.setup_notes = list(notes or [])
        self.elapsed_before = 0.0  # time used before a resume
        self.started = time.monotonic()
        self._resume_state = resume_state
        self._inputs_fp: list[dict[str, Any]] | None = None
        self.roots: list[Path] = []  # folders the run may read (set by the CLI)

    # ------------------------------------------------------------------ run
    async def run(self) -> Outcome:
        RUN_DEADLINE.set(self.deadline)
        pending = None
        if self._resume_state is not None:
            pending = self._restore(self._resume_state)
            self.trace.write({"type": "run_resumed", "steps_so_far": self.steps, "elapsed_before_s": round(self.elapsed_before, 2),
                              "pending_calls": [c["name"] for c in (pending or {}).get("calls", [])]})
        else:
            self.trace.write({"type": "run_start", "task": self.task, "files": [str(f) for f in self.files], "missing_inputs": self.missing,
                              "settings": self.settings.__dict__, "llm_model": getattr(self.llm, "model", None), "notes": self.setup_notes})
            guard = ProvenanceGuard()
            guard.add(self.task)
            self.ctx.provenance_guard = guard
            try:
                await asyncio.wait_for(self._preflight(guard), timeout=max(0.0, self._time_left()))
            except TimeoutError:
                return self._forced_stop(("time_limit", "Reached the time limit while inspecting the input files."))

        if self.llm is None:
            return self._stop("failed", "llm_not_configured", f"The planner LLM is not configured ({self.llm_error}). No task steps were attempted.")

        if not self.messages:
            self.messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message(self.task, self.inventory, self._budget_view(), BodhanKeys.from_env().availability(),
                                                         self.settings.max_steps)},
            ]
        if pending:  # a turn interrupted before its results were recorded: run its calls again (completed paid work is cached)
            calls = [ToolCall(c["id"], c["name"], c["arguments"], c.get("raw_arguments", "{}"), c.get("parse_error")) for c in pending["calls"]]
            await self._execute_turn(calls, pending.get("rationale", ""))
        nudges = 0
        while self.outcome is None:
            self._save()
            limit = self._limit_reached()
            if limit:
                return self._forced_stop(limit)
            last_step = self.steps >= self.settings.max_steps - 1
            tools = openai_tools({"finish"} if last_step else None)
            if last_step:
                self.messages.append({"role": "user", "content": "Only one step is left: call finish now with what you have (status partial if incomplete)."})
            est = self._llm_cost(prompt_token_bound(self.messages, tools), LLM_COMPLETION_RESERVE)
            if est is not None and not self.ctx.ledger.can_afford(est):  # priced planner: check before spending
                return self._forced_stop(("budget_exhausted", f"Not enough budget left for another planner call (up to about ₹{est:.3f})."))
            # priced: a retry is sent only if the budget still covers it after the attempts that may already have been billed
            can_retry = (lambda e=est: self.ctx.ledger.can_afford(e * (self.llm.uncertain_attempts + 1))) if est is not None else None
            try:
                reply = await asyncio.wait_for(self.llm.chat(self.messages, tools, max_tokens=LLM_COMPLETION_RESERVE if est is not None else None,
                                                             can_retry=can_retry), timeout=max(0.0, self._time_left()))
            except TimeoutError:
                sent = getattr(self.llm, "uncertain_attempts", 0)  # includes a request cut off in flight, not one waiting to retry
                if est is not None and sent:
                    self._charge_uncertain_llm(est, sent, "timeout")
                return self._forced_stop(("time_limit", f"Reached the {self.settings.max_seconds:.0f}s time limit during a planner call."))
            except LLMError as exc:
                sent = getattr(self.llm, "uncertain_attempts", 0)
                if est is not None and sent:  # priced: failed attempts may have been billed; count them conservatively
                    self.ctx.ledger.add_llm(0, 0, est * sent, calls=0)
                self.trace.write({"type": "llm_error", "step": self.steps, "error": str(exc), "uncertain_attempts": sent,
                                  "cost_inr": round(est * sent, 5) if est is not None and sent else None})
                return self._forced_stop(("llm_error", f"The planner LLM failed: {exc}"))
            if est is not None and getattr(self.llm, "uncertain_attempts", 0):  # failed attempts before this success
                self._charge_uncertain_llm(est, self.llm.uncertain_attempts, "retried")
            cost = self._llm_cost(reply.prompt_tokens, reply.completion_tokens)
            self.ctx.ledger.add_llm(reply.prompt_tokens, reply.completion_tokens, cost)
            if est is not None and cost is not None and cost > est:  # should not happen; if it does, say so rather than hide it
                self.ctx.ledger.llm_over_bound += cost - est
                self.trace.write({"type": "llm_estimate_exceeded", "step": self.steps, "estimate_inr": round(est, 5), "cost_inr": round(cost, 5)})
            self.trace.write({"type": "llm_call", "step": self.steps, "latency_s": round(reply.latency_s, 2), "prompt_tokens": reply.prompt_tokens,
                              "completion_tokens": reply.completion_tokens, "cost_inr": cost, "finish_reason": reply.finish_reason,
                              "decision": [c.name for c in reply.tool_calls] or "no tool call", "rationale": reply.content[:600],
                              "fallback_text_tool_call": reply.fallback_parsed})
            self.messages.append(reply.assistant_message)
            if not reply.tool_calls:
                nudges += 1
                self.steps += 1
                if nudges > 2:
                    return self._forced_stop(("no_progress", "The planner stopped calling tools without finishing."))
                self.messages.append({"role": "user", "content": "Continue by calling a tool, or call finish(...) to end the run."})
                continue
            nudges = 0
            self._save(pending={"calls": [c.__dict__ for c in reply.tool_calls], "rationale": reply.content})
            await self._execute_turn(reply.tool_calls, reply.content)
            if self.budget_errors >= 3 and self.outcome is None:
                return self._forced_stop(("budget_exhausted", "Repeated budget errors; stopped to stay within --max-cost-inr."))
        self._save()
        return self.outcome

    async def _execute_turn(self, calls: list[ToolCall], rationale: str) -> None:
        """Run one turn's calls: plans first, independent tools in parallel, finish last.
        A turn may not use more steps than remain; extra calls are refused, not run."""
        order = ([c for c in calls if c.name == "record_plan"] + [c for c in calls if c.name not in ("record_plan", "finish")]
                 + [c for c in calls if c.name == "finish"])
        room = max(0, self.settings.max_steps - self.steps)
        # The last step is reserved for finish: other calls may use at most room - 1 steps.
        others = [c for c in order if c.name != "finish"]
        finishes_all = [c for c in order if c.name == "finish"]
        admitted_others = others[:max(0, room - 1)]
        admitted = admitted_others + finishes_all[:max(0, room - len(admitted_others))][:1]
        refused = [c for c in order if c not in admitted]
        results: dict[str, dict] = {c.id: {"ok": False, "error": {"code": "step_limit", "message": f"--max-steps {self.settings.max_steps} reached; "
                                                                  "this call was not run. Call finish."}} for c in refused}
        if not admitted:
            self.steps += 1  # a turn whose calls were all refused still uses a step, so a planner ignoring the limit cannot loop forever
        plans = [c for c in admitted if c.name == "record_plan"]
        work = [c for c in admitted if c.name not in ("record_plan", "finish")]
        finishes = [c for c in admitted if c.name == "finish"]
        for c in plans:
            self.steps += 1
            results[c.id] = self._record_plan(c)
        if work:
            first = self.steps + 1
            self.steps += len(work)
            outs = await asyncio.gather(*[self._run_tool(c, rationale, first + i) for i, c in enumerate(work)])
            results.update({c.id: o for c, o in zip(work, outs)})
        for c in finishes:
            self.steps += 1
            results[c.id] = self._finish(c, others_in_turn=bool(work) or len(finishes) > 1)
            if self.outcome is not None:
                break
        for c in calls:
            if c.id in results:
                self.messages.append({"role": "tool", "tool_call_id": c.id, "content": _clip(results[c.id])})
        self._save()

    # -------------------------------------------------------------- pieces
    async def _preflight(self, guard: ProvenanceGuard) -> None:
        t0 = time.monotonic()
        spec = TOOL_BY_NAME["inspect_files"]
        listed: list[dict] = []
        if self.files:
            result, _ = await invoke(spec, self.ctx, {"paths": [str(f) for f in self.files]})
            listed = result.get("files", [])
        for f in self.files:
            if classify(f) == "text":
                try:
                    guard.add(read_text_file(f))
                except E.ToolError:
                    pass
        self.inventory = {"files": listed, "missing_inputs": self.missing,
                          "note": "No input files were given." if not self.files and not self.missing else ""}
        self._inputs_fp = await asyncio.to_thread(checkpoint.fingerprint_inputs, self.files)
        self.trace.write({"type": "preflight", "step": 0, "tool": "inspect_files", "files": len(listed), "missing": self.missing,
                          "result_summary": [{k: f.get(k) for k in ("name", "kind", "duration_s", "pages", "chars")} for f in listed][:50],
                          "latency_s": round(time.monotonic() - t0, 3), "cost_inr": 0.0})

    def _record_plan(self, call: ToolCall) -> dict:
        if call.parse_error:
            return self._control_error(call, f"Arguments are not valid JSON: {call.parse_error}. The previous plan (if any) is unchanged.")
        try:
            plan = RecordPlanIn.model_validate(call.arguments)
        except ValidationError as exc:
            return self._control_error(call, f"Invalid plan: {_validation_message(exc)}. The previous plan (if any) is unchanged.")
        texts = plan.steps + plan.assumptions + [x for d in plan.deliverables for x in (d.description, d.language or "", d.source or "")]
        if not all(self._indic_ok(t) for t in texts):
            return self._control_error(call, "The plan contains Indian-language text you wrote. Write the plan in English.", E.PROVENANCE_VIOLATION)
        for d in plan.deliverables:
            if d.language:
                d.language = normalize_language(d.language)
                if d.language not in LANGUAGE_NAMES:
                    return self._control_error(call, f"Deliverable language {d.language!r} is not a language code Bodhan supports "
                                               "(use e.g. 'ta', 'hi', 'en'). If the task asks for an unsupported language, finish as unsupported.")
        self.plan = plan.model_dump()
        self.plan_version += 1
        self.trace.write({"type": "plan", "step": self.steps, "version": self.plan_version, **self.plan})
        return {"ok": True, "plan_version": self.plan_version, "note": "Plan recorded. Execute it."}

    def _control_error(self, call: ToolCall, message: str, code: str = E.INVALID_ARGUMENT) -> dict:
        self.trace.write({"type": "control_rejected", "step": self.steps, "tool": call.name, "error": message[:500]})
        return {"ok": False, "error": {"code": code, "message": message, "retryable": True, "hint": f"See the {call.name} schema."}}

    async def _run_tool(self, call: ToolCall, rationale: str, step: int) -> dict:
        spec = TOOL_BY_NAME.get(call.name)
        t0 = time.monotonic()
        scope: list[float] = []  # owned here, so the spend of a call cut off by the deadline is still reported
        if call.parse_error:
            result = {"ok": False, "error": {"code": E.INVALID_ARGUMENT, "message": call.parse_error, "hint": "Send arguments as a JSON object."}}
        elif spec is None:
            result = {"ok": False, "error": {"code": "unknown_tool", "message": f"No tool named {call.name!r}."}}
        elif spec.paid and self.plan is None:
            result = {"ok": False, "error": {"code": "plan_required", "message": "Call record_plan (with deliverables) before using paid tools."}}
        else:
            CURRENT_STEP.set(step)
            try:  # a tool may not outlive the run's deadline; cancellation releases unsent reservations
                result, _ = await asyncio.wait_for(invoke(spec, self.ctx, call.arguments, scope=scope), timeout=max(0.0, self._time_left()))
            except TimeoutError:
                await self.ctx.client.cancel_all()  # the run is ending: settle in-flight requests so this step's cost is known
                result = {"ok": False, "error": {"code": "time_limit", "message": "The run's time limit was reached while this tool was running.",
                                                 "retryable": False, "hint": "Finish with what you have."}, "cost_inr": round(sum(scope), 5)}
        if not result.get("ok") and (result.get("error") or {}).get("code") == E.BUDGET_EXCEEDED:
            self.budget_errors += 1
        entry = {"type": "tool_call", "step": step, "plan_version": self.plan_version, "rationale": rationale[:400], "tool": call.name,
                 "arguments": call.arguments, "ok": bool(result.get("ok")), "result_summary": summarize_result(result),
                 "latency_s": round(time.monotonic() - t0, 3), "cost_inr": result.get("cost_inr", round(sum(scope), 5)),
                 "budget_remaining_inr": result.get("budget_remaining_inr")}
        self.trace.write(entry)
        self.tool_log.append(entry)
        return result

    def _resolve_deliverable(self, raw: str) -> Artifact | None:
        raw = str(raw).strip()
        arts = self.ctx.artifacts
        cand = Path(raw)
        for key in (str(cand), str(cand.resolve()) if cand.is_absolute() else "", str((self.ctx.artifacts_dir / cand.name).resolve()),
                    str((self.ctx.artifacts_dir.parent / cand).resolve())):
            if key and key in arts:
                return arts[key]
        return None

    def _finish(self, call: ToolCall, *, others_in_turn: bool) -> dict:
        if call.parse_error:
            return self._control_error(call, f"Arguments are not valid JSON: {call.parse_error}.")
        try:
            a = FinishIn.model_validate(call.arguments)
        except ValidationError as exc:
            return self._control_error(call, f"Invalid finish call: {_validation_message(exc)}.")
        status, answer = a.status, a.final_answer.strip()
        reasons: list[str] = []
        if others_in_turn:
            reasons.append("finish was called in the same turn as other tools; check their results first, then call finish alone")
        indic_fields = [f for f, v in [("final_answer", answer)] + [("assumptions", x) for x in a.assumptions]
                        + [("limitations", x) for x in a.limitations] if not self._indic_ok(v)]
        if indic_fields:
            reasons.append(f"{', '.join(sorted(set(indic_fields)))} contain Indian-language text you wrote; write English only and deliver Indic text as artifacts")
        delivered, unknown = [], []
        for d in a.deliverables:
            art = self._resolve_deliverable(d)
            (delivered if art is not None and art.path.is_file() else unknown).append(art if art is not None and art.path.is_file() else d)
        if unknown:
            reasons.append(f"deliverables not produced by a tool in this run: {[self._safe(str(u)) for u in unknown]}")
        unverified = [x.path.name for x in self.ctx.artifacts.values() if x.kind in VERIFIED_KINDS and x.verification is None]
        if unverified:
            reasons.append(f"generated translations/audio without verification: {unverified}; verify them (verify_translation/verify_speech)")
        failing = [x for x in delivered if self._effective_status(x) == "fail"]
        if status == "completed":
            if failing:
                reasons.append("deliverables failed verification (directly or through what they were made from): "
                               + "; ".join(x.path.name for x in failing) + ". Repair them differently, or finish with status partial and explain.")
            reasons += self._contract_gaps(delivered, answer)
        if reasons and self.finish_rejections < 2:
            self.finish_rejections += 1
            self.trace.write({"type": "finish_rejected", "step": self.steps, "reasons": reasons})
            return {"ok": False, "error": {"code": "finish_rejected", "message": "Cannot finish yet: " + " | ".join(reasons)}}
        limitations, assumptions = list(a.limitations), list(a.assumptions)
        if indic_fields:  # never relaxed: replace planner-written Indic text with a code-written English note
            note = "[removed: the planner wrote Indian-language text here, which this agent does not allow]"
            if not self._indic_ok(answer):
                answer = ("The planner's final answer contained Indian-language text it wrote itself, so it was withheld. "
                          "See the deliverables below; only Bodhan-generated text is delivered.")
            limitations = [x if self._indic_ok(x) else note for x in limitations]
            assumptions = [x if self._indic_ok(x) else note for x in assumptions]
        usable = [x for x in delivered if self._usable(x)]
        if reasons:  # accepted after repeated rejection: downgrade honestly
            limitations += [f"verification gate: {r}" for r in reasons]
            if status == "completed":
                status = "partial" if usable else "failed"
        final = status
        if status == "completed" and any(self._effective_status(x) == "warn" for x in usable):
            final = "completed_with_warnings"
        for x in delivered:
            if x not in usable:
                limitations.append(f"not delivered as usable: {x.path.name} ({self._effective_status(x) or 'unverified'})")
        self.outcome = Outcome(final, "finished", answer, [str(x.path) for x in usable], assumptions, limitations, planner_status=a.status)
        self.trace.write({"type": "finish", "step": self.steps, "status": final, "planner_status": a.status,
                          "deliverables": self.outcome.deliverables, "gate_reasons": reasons})
        return {"ok": True, "status": final}

    def _contract_gaps(self, delivered: list[Artifact], answer: str) -> list[str]:
        """Each deliverable the plan declared must be matched by an existing, non-failing
        artifact of a compatible kind and language. Generic: no task-specific rules."""
        if self.plan is None:
            return ["status completed needs a recorded plan with its deliverables"]
        gaps, used = [], set()
        for spec in self.plan["deliverables"]:
            kind, lang, src = spec["kind"], spec.get("language"), (spec.get("source") or "").strip()
            label = f"{kind}{' (' + lang + ')' if lang else ''}{' from ' + src if src else ''}"                     f"{': ' + spec['description'][:60] if spec.get('description') else ''}"
            if kind == "answer":
                if not answer or not any(t["ok"] for t in self.tool_log):
                    gaps.append(f"declared deliverable '{label}' needs an answer based on at least one successful tool call")
                continue
            # one artifact per declared deliverable; a declared source must be in the artifact's input lineage
            match = next((x for x in delivered if str(x.path) not in used and x.kind in KIND_MATCH[kind] and (not lang or x.language == lang)
                          and (not src or Path(src).name.lower() in {o.lower() for o in x.origin}) and self._usable(x)), None)
            if match is not None:
                used.add(str(match.path))
            elif not spec.get("optional"):
                gaps.append(f"declared deliverable '{label}' has no matching verified artifact among the finish deliverables "
                            "(each declared deliverable needs its own artifact)")
        return gaps

    def _usable(self, art: Artifact) -> bool:
        """The one rule for presenting an artifact as a usable output, on every stop path."""
        return (art.path.is_file() and self._effective_status(art) != "fail"
                and not (art.kind in VERIFIED_KINDS and art.verification is None))

    def _indic_ok(self, text: str) -> bool:
        """Indian-language text in the planner's own words is allowed only when quoted
        verbatim from the task, an input or a Bodhan output (checked as rendered)."""
        try:
            self.ctx.provenance_guard.check(text, "planner text")
            return True
        except E.ToolError:
            return False

    def _safe(self, text: str) -> str:
        return text if self._indic_ok(text) else "<non-English text>"

    def _effective_status(self, art: Artifact, seen: set[str] | None = None) -> str | None:
        """Worst verification status of an artifact and everything it was made from or embeds."""
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
            st = self._effective_status(child, seen) if child is not None else "fail"  # an unknown source cannot vouch
            if order.get(st, 0) > order.get(worst, 0):
                worst = st
        return worst

    def _time_left(self) -> float:
        return self.deadline - time.monotonic()

    # --------------------------------------------------------------- stops
    def _limit_reached(self) -> tuple[str, str] | None:
        if self.steps >= self.settings.max_steps:
            return ("step_limit", f"Reached --max-steps {self.settings.max_steps}.")
        if self._time_left() <= 0:
            return ("time_limit", f"Reached the {self.settings.max_seconds:.0f}s time limit.")
        rem = self.ctx.ledger.remaining
        if rem is not None and rem <= 0.0005:
            return ("budget_exhausted", "The --max-cost-inr budget is used up.")
        return None

    def forced_outcome(self, reason: str, text: str) -> Outcome:
        """Truthful outcome when the planner did not finish (limits, outage, crash)."""
        produced = [x for x in self.ctx.artifacts.values() if x.kind in OUTPUT_KINDS]
        good = [x for x in produced if self._usable(x)]
        bad = [x for x in produced if x not in good]
        status = "partial" if good else "failed"
        answer = (f"The run stopped before the planner finished: {text} "
                  + (f"{len(good)} artifact(s) passed their checks and are listed below; they may not fully answer the task."
                     if good else "No usable output was produced."))
        limitations = [f"Stopped early ({reason}); outputs were not confirmed complete by the planner."]
        limitations += [f"not delivered as usable: {x.path.name} ({self._effective_status(x) or 'unverified'})" for x in bad]
        self.outcome = Outcome(status, reason, answer, [str(x.path) for x in good], list((self.plan or {}).get("assumptions", [])), limitations)
        return self.outcome

    def _forced_stop(self, limit: tuple[str, str]) -> Outcome:
        outcome = self.forced_outcome(*limit)
        self.trace.write({"type": "stop", "step": self.steps, "status": outcome.status, "reason": outcome.stop_reason, "message": outcome.final_answer})
        self._save()
        return outcome

    def _stop(self, status: str, reason: str, answer: str) -> Outcome:
        self.outcome = Outcome(status, reason, answer, [], list((self.plan or {}).get("assumptions", [])), [])
        self.trace.write({"type": "stop", "step": self.steps, "status": status, "reason": reason, "message": answer})
        self._save()
        return self.outcome

    # ----------------------------------------------------------- checkpoints
    def _save(self, pending: dict | None = None) -> None:
        if self.out is None:
            return
        guard = self.ctx.provenance_guard
        st = {"task": self.task, "inputs": self._inputs_fp or [], "missing": self.missing, "roots": [str(r) for r in self.roots], "settings": self.settings.__dict__,
              "llm_model": getattr(self.llm, "model", None), "steps": self.steps, "plan": self.plan, "plan_version": self.plan_version,
              "finish_rejections": self.finish_rejections, "budget_errors": self.budget_errors, "tool_log": self.tool_log,
              "messages": self.messages, "inventory": self.inventory, "pending": pending, "ledger": self.ctx.ledger.to_state(),
              "artifacts": [a.to_state() for a in self.ctx.artifacts.values()], "transcripts": {k: str(v) for k, v in self.ctx.transcripts.items()},
              "provenance": list(getattr(guard, "_corpus", [])), "elapsed_s": self.elapsed_before + (time.monotonic() - self.started),
              "finished": self.outcome is not None, "status": self.outcome.status if self.outcome else None}
        try:
            checkpoint.save(self.out, st)
        except OSError as exc:  # a checkpoint is best effort; the run itself continues
            self.trace.write({"type": "checkpoint_error", "error": repr(exc)})

    def _restore(self, st: dict[str, Any]) -> dict | None:
        self.steps, self.plan, self.plan_version = st["steps"], st["plan"], st["plan_version"]
        self.finish_rejections, self.budget_errors = st["finish_rejections"], st["budget_errors"]
        self.tool_log, self.messages, self.inventory = st["tool_log"], st["messages"], st.get("inventory", {})
        self.missing, self._inputs_fp = st.get("missing", []), st.get("inputs", [])
        self.elapsed_before = float(st.get("elapsed_s", 0.0))
        self.ctx.ledger.load_state(st["ledger"])
        rebuilt = checkpoint.ledger_from_trace(self.out / "trace.jsonl") if self.out else None
        if rebuilt is not None:  # the trace is written as each request settles, so it is the more complete record
            self.ctx.ledger.load_state(rebuilt)
        guard = ProvenanceGuard()
        guard._corpus = list(st.get("provenance", []))
        self.ctx.provenance_guard = guard
        dropped = []
        for item in st.get("artifacts", []):
            art = Artifact.from_state(item)
            if not art.path.is_file() or (art.digest and checkpoint.file_sha256(art.path) != art.digest):
                # Changed or missing since the checkpoint: kept as a failed record, so anything made from it
                # stays unusable too (dropping it would hide that failure from its descendants).
                dropped.append(art.path.name)
                art.verification = {"status": "fail", "method": "resume integrity check",
                                    "problems": ["the file changed or disappeared after the checkpoint"], "warnings": []}
            self.ctx.artifacts[str(art.path)] = art
        self.ctx.transcripts.update({k: Path(v) for k, v in st.get("transcripts", {}).items() if str(v) in self.ctx.artifacts})
        if dropped:
            self.setup_notes.append(f"artifacts changed or missing since the checkpoint are marked failed: {dropped}")
        return st.get("pending")

    # ----------------------------------------------------------------- misc
    def _budget_view(self) -> dict:
        return {"max_cost_inr": self.settings.max_cost_inr, "prices": {"indic-transcribe": "₹0.10/min", "indic-translate": "₹0.20/10k output tokens",
                "indic-speak": "₹6/10k chars", "indic-ocr": "₹0.20/page"}}

    def _charge_uncertain_llm(self, est: float, attempts: int, why: str) -> None:
        """Priced planner attempts that got no usable reply may still have been billed: charge each at the call's estimate."""
        self.ctx.ledger.add_llm(0, 0, est * attempts, calls=0)
        self.trace.write({"type": "llm_uncertain", "step": self.steps, "reason": why, "attempts": attempts, "cost_inr": round(est * attempts, 5)})

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
