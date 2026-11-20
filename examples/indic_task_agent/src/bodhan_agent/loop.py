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
from bodhan_mcp.languages import LANGUAGE_NAMES, SPEAK_LANGUAGES, TRANSLATE_LANGUAGES, normalize_language
from bodhan_mcp.registry import clean_schema, invoke
from bodhan_mcp.runtime import RUN_DEADLINE, Artifact, RunContext, is_network_path
from bodhan_mcp.tools import TOOL_BY_NAME, TOOLS
from bodhan_mcp.tools.common import ProvenanceGuard

from . import state as checkpoint
from .llm import ContextTooLong, LLMError, PlannerLLM, ToolCall
from .prompts import SYSTEM_PROMPT, user_message
from .trace import CURRENT_STEP, TraceWriter, summarize_result

PAID_TOOLS = {t.name for t in TOOLS if t.paid}
VERIFIED_KINDS = {"translation", "audio", "subtitles"}
OUTPUT_KINDS = VERIFIED_KINDS | {"text", "ocr", "transcript"}
FINISH_STATUSES = ["completed", "partial", "failed", "unsupported"]
MAX_TOOL_RESULT_CHARS = 7000
CONTEXT_BYTES = 90_000  # about 24k tokens of English: older tool results are shortened beyond this (fits 32k-context planners)
KEEP_RECENT_TOOL_RESULTS = 6
LLM_COMPLETION_RESERVE = 4000
COVERAGE_COMPLETION_RESERVE = 1000  # the coverage check's reply is a short list
COVERAGE_TIMEOUT_S = 90.0  # completion tokens allowed per planner call when its price is known
# Upper bound on a priced planner call's prompt tokens: byte-level BPE and byte-fallback tokenizers never make a token
# smaller than one UTF-8 byte, so tokens <= bytes. Margins cover per-message special tokens and the provider's own
# formatting of the tool list. (A character count is not a bound: one Indic character is 3 bytes and may be 3 tokens.)
PROMPT_TOKENS_PER_MESSAGE = 16
PROMPT_TOKENS_MARGIN = 1000


def _without_nulls(value):
    """null for an optional field means "not given" (many models send it); applied inside lists and objects too."""
    if isinstance(value, dict):
        return {k: _without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_nulls(v) for v in value if v is not None]
    return value


def prompt_token_bound(messages: list[dict], tools: list[dict]) -> int:
    body = json.dumps(messages, ensure_ascii=False).encode("utf-8") + json.dumps(tools, ensure_ascii=False).encode("utf-8")
    return len(body) + PROMPT_TOKENS_PER_MESSAGE * len(messages) + PROMPT_TOKENS_MARGIN

# Which artifact kinds satisfy a declared deliverable kind.
KIND_MATCH = {"audio": {"audio"}, "translation": {"translation", "subtitles"}, "subtitles": {"subtitles"},
              "text": {"text", "translation", "ocr", "transcript"}, "transcript": {"transcript", "subtitles"}}


def _src(spec: dict) -> str:
    return Path((spec.get("source") or "").strip()).name.lower()


def _keeps(new: dict, old: dict) -> bool:
    """True when an artifact that satisfies the required deliverable `new` also satisfies the earlier obligation `old`."""
    return (not new.get("optional") and new["kind"] in KIND_MATCH.get(old["kind"], {old["kind"]})
            and new.get("language") == old.get("language") and (not _src(old) or _src(new) == _src(old)))


def _names(dropped: dict, old: dict) -> bool:
    return dropped["kind"] == old["kind"] and dropped.get("language") == old.get("language")


def _item_name(text: str | None) -> str:
    """The item name in a coverage reply: models often echo the whole listed line ("x_mr.md: translation, Marathi")."""
    t = (text or "").strip().strip("`'\"").removeprefix("- ").strip()
    t = t.replace("\\", "/").rsplit("/", 1)[-1]  # a full path: its file name
    return t.split(":", 1)[0].strip().strip("`'\"") if ":" in t else t


def _label(spec: dict) -> str:
    return f"{spec['kind']}{' (' + spec['language'] + ')' if spec.get('language') else ''}{' from ' + spec['source'] if spec.get('source') else ''}"


# ---------------------------------------------------------------------------
# Typed control calls
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliverableSpec(_Strict):
    kind: Literal["audio", "translation", "subtitles", "text", "transcript", "answer"] = Field(
        description="audio, translation, subtitles, text (a written summary/report/list), transcript, or answer (no file: the reply itself)")
    language: str | None = Field(None, max_length=16, description="Language code of the output, e.g. 'ta' (omit if not language-specific). "
                                                                 "For romanized output use the language code (e.g. 'hi') and say so in the description.")
    source: str | None = Field(None, max_length=200, description="Input file name this output is made from, when the task asks for one output per file.")
    optional: bool = Field(False, description="True for conditional outputs that may legitimately not exist (e.g. 'translate the Telugu clips' when none is Telugu).")
    description: str = Field("", max_length=300, description="What this output is, e.g. 'two-minute Tamil summary audio'.")


class DroppedSpec(_Strict):
    kind: Literal["audio", "translation", "subtitles", "text", "transcript"]
    language: str | None = Field(None, max_length=16)
    reason: str = Field(min_length=1, max_length=300, description="Why this output from an earlier plan is no longer produced.")


class RecordPlanIn(_Strict):
    steps: list[str] = Field(min_length=1, max_length=40, description="Numbered steps you will take, each naming the tool.")
    deliverables: list[DeliverableSpec] = Field(min_length=1, max_length=40,  # "every supported language" is 23
                                                description="Every output the task asks for. Use kind 'answer' when the reply itself is the output.")
    assumptions: list[str] = Field(default_factory=list, max_length=20, description="Assumptions made because the task was ambiguous.")
    dropped: list[DroppedSpec] = Field(default_factory=list, max_length=40, description=(
        "When revising a plan: each required output of an earlier plan that this plan no longer produces, with the reason. "
        "Dropped outputs are reported to the user."))


class CoverageItem(BaseModel):  # a reply to parse, not a planner action: extra keys are ignored
    request: str = Field(max_length=300, description="The words of the task that ask for this output.")
    covered_by: str | list[str] | None = Field(None, max_length=300, description="The name of the delivered item that provides it (e.g. 'answer' or a file name), or null.")


class CoverageIn(BaseModel):
    outputs: list[CoverageItem] = Field(max_length=40, description="One entry per distinct output the task asks for.")


COVERAGE_TOOL = {"type": "function", "function": {"name": "report_coverage", "description": "Report which requested outputs were delivered.",
                                                  "parameters": clean_schema(CoverageIn.model_json_schema())}}
COVERAGE_PROMPT = """\
You check a finished task for an unattended agent. You did not do the work. Read the user's task and the list of \
delivered items, then call report_coverage with one entry for each distinct output the task asks for (a file, audio, \
subtitles, a translation into a language, or an answer to a question). For each, give the delivered item that \
provides it, by its name only (the part before the colon: "answer" or a file name), or null if none does. An item provides an output only if it has the requested form \
(spoken output needs audio; subtitles need an .srt; "a file" needs a file) and the requested language. A conditional \
request ("translate the Telugu ones") is provided by the answer when it explains that nothing qualified. Do not list \
outputs the task did not ask for, and do not judge quality."""


class FinishIn(_Strict):
    status: Literal["completed", "partial", "failed", "unsupported"] = Field(
        description="completed only if every declared deliverable exists and passed verification.")
    final_answer: str = Field(min_length=1, max_length=6000, description="English answer/explanation for the user (no Indian-language text).")
    deliverables: list[str] = Field(default_factory=list, max_length=40, description="Artifact paths that answer the task.")
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=30, description="Honest limitations, failures, skipped parts.")


CONTROL_SPECS = {
    "record_plan": ("Record or revise the plan before acting: steps and the deliverables the task asks for. Required before any paid tool.",
                    RecordPlanIn),
    "finish": ("End the run. Deliverables must be artifact paths returned by tools in this run.", FinishIn),
}
CONTROL_TOOLS = [{"type": "function", "function": {"name": n, "description": d, "parameters": clean_schema(m.model_json_schema())}}
                 for n, (d, m) in CONTROL_SPECS.items()]


def openai_tools(only: set[str] | None = None, compact: bool = False) -> list[dict[str, Any]]:
    """The tool list for the planner. `compact` keeps names, types and each tool's first sentence only (about half the
    size), for endpoints that refuse long requests (e.g. 8k-token caps)."""
    specs = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema()}} for t in TOOLS]
    out = [t for t in specs + CONTROL_TOOLS if only is None or t["function"]["name"] in only]
    if not compact:
        return out
    return [{"type": "function", "function": {"name": t["function"]["name"], "description": _first_sentence(t["function"].get("description", "")),
                                              "parameters": _without_descriptions(t["function"]["parameters"])}} for t in out]


def _first_sentence(text: str) -> str:
    end = text.find(". ")
    return (text[: end + 1] if end > 0 else text)[:200]


def _without_descriptions(node: Any, field_names: bool = False) -> Any:
    """Drop "description" annotations from a schema. In a "properties" map the keys are field names, and a field
    may itself be called "description", so those keys are all kept."""
    if isinstance(node, dict):
        if field_names:
            return {k: _without_descriptions(v) for k, v in node.items()}
        return {k: _without_descriptions(v, field_names=(k == "properties")) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [_without_descriptions(x) for x in node]
    return node


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
                    if not x.resolve().is_relative_to(p):  # a link inside the folder pointing elsewhere is not an input
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
        # Required outputs of every plan so far: a revised plan may not leave one out silently (see _record_plan)
        self.obligations: list[dict[str, Any]] = []
        self.dropped: list[dict[str, Any]] = []
        self.outcome: Outcome | None = None
        self.finish_rejections = 0
        self.budget_errors = 0
        self.compact_tools = False  # set when the endpoint refuses even a compacted request
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
        nudges = compactions = 0
        while self.outcome is None:
            self._save()
            limit = self._limit_reached()
            if limit:
                return self._forced_stop(limit)
            last_step = self.steps >= self.settings.max_steps - 1
            tools = openai_tools({"finish"} if last_step else None, compact=self.compact_tools)
            if last_step:
                self.messages.append({"role": "user", "content": "Only one step is left: call finish now with what you have (status partial if incomplete)."})
            if self._compact(level=0):
                self.trace.write({"type": "context_compacted", "step": self.steps, "reason": "conversation over the context budget"})
            est = self._llm_cost(prompt_token_bound(self.messages, tools), LLM_COMPLETION_RESERVE)
            if est is not None and not self.ctx.ledger.can_afford(est):  # priced planner: check before spending
                return self._forced_stop(("budget_exhausted", f"Not enough budget left for another planner call (up to about ₹{est:.3f})."))
            # priced: a retry is sent only if the budget still covers it after the attempts that may already have been billed
            can_retry = (lambda e=est: self.ctx.ledger.can_afford(e * (self.llm.uncertain_attempts + 1))) if est is not None else None
            if est is not None:  # if the process dies during the call, resume charges it from this record
                self.trace.write({"type": "llm_dispatch", "step": self.steps, "estimate_inr": round(est, 5),
                                  "max_attempts": getattr(self.llm, "max_attempts", 1)})
            try:
                reply = await asyncio.wait_for(self.llm.chat(self.messages, tools, max_tokens=LLM_COMPLETION_RESERVE if est is not None else None,
                                                             can_retry=can_retry), timeout=max(0.0, self._time_left()))
            except TimeoutError:
                sent = getattr(self.llm, "uncertain_attempts", 0)  # includes a request cut off in flight, not one waiting to retry
                if est is not None and sent:
                    self._charge_uncertain_llm(est, sent, "timeout")
                return self._forced_stop(("time_limit", f"Reached the {self.settings.max_seconds:.0f}s time limit during a planner call."))
            except ContextTooLong as exc:
                if est is not None and getattr(self.llm, "uncertain_attempts", 0):  # earlier failed attempts may be billed
                    self._charge_uncertain_llm(est, self.llm.uncertain_attempts, "before_context_rejection")
                shrunk = False
                while compactions < 3 and not shrunk:  # escalate until a level actually shortens the conversation
                    compactions += 1
                    shrunk = self._compact(level=compactions)
                if shrunk:
                    self.trace.write({"type": "context_compacted", "step": self.steps, "reason": str(exc)[:200]})
                    continue
                if not self.compact_tools:  # the fixed part is too big: send the short form of the tool list from now on
                    self.compact_tools = True
                    getattr(self.llm, "notes", []).append("the planner endpoint refused long requests; the short tool list is used")
                    self.trace.write({"type": "context_compacted", "step": self.steps, "reason": "short tool list: " + str(exc)[:160]})
                    continue
                sent = getattr(self.llm, "uncertain_attempts", 0)
                self.trace.write({"type": "llm_error", "step": self.steps, "error": str(exc), "uncertain_attempts": sent})
                return self._forced_stop(("llm_error", f"The planner LLM failed: {exc}"))
            except LLMError as exc:
                sent = getattr(self.llm, "uncertain_attempts", 0)
                if est is not None and sent:  # priced: failed attempts may have been billed; count them conservatively
                    self.ctx.ledger.add_llm(0, 0, est * sent, calls=0)
                self.trace.write({"type": "llm_error", "step": self.steps, "error": str(exc), "uncertain_attempts": sent,
                                  "cost_inr": round(est * sent, 5) if est is not None and sent else None})
                return self._forced_stop(("llm_error", f"The planner LLM failed: {exc}"))
            compactions = 0  # a reply arrived: the next "too long" rejection may compact again
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
                    if reply.content.strip() and self.plan is not None:
                        # The planner keeps answering in plain text: take that text as its final answer, as "partial"
                        # (it never said the task is complete; an explicit {"status": ...} would have been parsed as finish).
                        usable = [str(a.path) for a in self.ctx.artifacts.values() if a.kind in OUTPUT_KINDS and self._usable(a)]
                        salvage = ToolCall(f"salvaged_{self.steps}", "finish",
                                           {"status": "partial", "final_answer": reply.content.strip(), "deliverables": usable},
                                           json.dumps({"status": "completed"}))
                        self.trace.write({"type": "finish_salvaged", "step": self.steps, "deliverables": usable})
                        self.messages[-1] = {**self.messages[-1], "tool_calls": [{"id": salvage.id, "type": "function", "function":
                                                                                     {"name": "finish", "arguments": salvage.raw_arguments}}]}
                        self.steps -= 1  # the plain-text reply's step becomes the finish step
                        await self._execute_turn([salvage], reply.content)
                        if self.outcome is not None:
                            return self.outcome
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
            results[c.id] = await self._finish(c, others_in_turn=bool(work) or len(finishes) > 1)
            if self.outcome is not None:
                break
        for c in calls:
            if c.id in results:
                content = _clip(results[c.id])
                r = results[c.id]
                if c.name == "read_text" and r.get("ok") and len(content) != len(json.dumps(r, ensure_ascii=False, default=str)):
                    self.ctx.forget_read(r["path"], r["offset"], r["offset"] + r["returned_chars"])  # clipped: not fully seen
                self.messages.append({"role": "tool", "tool_call_id": c.id, "content": content})
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
            guard.add(f.name)  # a file name is the user's text: the answer may name an Indian-language file
            guard.add(f.parent.name)
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
            plan = RecordPlanIn.model_validate(_without_nulls(call.arguments))
        except ValidationError as exc:
            return self._control_error(call, f"Invalid plan: {_validation_message(exc)}. The previous plan (if any) is unchanged.")
        texts = plan.steps + plan.assumptions + [x for d in plan.deliverables for x in (d.description, d.language or "", d.source or "")]
        if not all(self._indic_ok(t) for t in texts):
            return self._control_error(call, "The plan contains Indian-language text you wrote. Write the plan in English.", E.PROVENANCE_VIOLATION)
        for d in plan.deliverables:
            if d.language:
                base, _, script = d.language.replace("_", "-").partition("-")
                if script.lower() in ("latn", "roman", "romanized", "latin"):  # a transliteration request: same language, Latin script
                    d.language = base
                    d.description = (d.description + " (romanized)").strip()
                d.language = normalize_language(d.language)
                if d.language not in LANGUAGE_NAMES:
                    return self._control_error(call, f"Deliverable language {d.language!r} is not a language code Bodhan supports "
                                               "(use e.g. 'ta', 'hi', 'en'). If the task asks for an unsupported language, finish as unsupported.")
                can = {"audio": SPEAK_LANGUAGES | {"en"}, "translation": TRANSLATE_LANGUAGES}.get(d.kind)
                if can is not None and d.language not in can:
                    name = LANGUAGE_NAMES.get(d.language, d.language)
                    return self._control_error(call, f"Bodhan cannot produce {d.kind} in {name} ({d.language}). Leave it out of the plan. "
                                               "If nothing else the task asks for is possible, finish as unsupported; otherwise deliver the "
                                               f"rest and finish partial, saying that {name} {d.kind} is not available.")
        for d in plan.dropped:
            d.language = normalize_language(d.language) if d.language else None
        if not all(self._indic_ok(d.reason) for d in plan.dropped):
            return self._control_error(call, "The dropped reasons contain Indian-language text you wrote. Write them in English.", E.PROVENANCE_VIOLATION)
        new = [d.model_dump() for d in plan.deliverables]
        dropped = [d.model_dump() for d in plan.dropped]
        left_out = [o for o in self.obligations if not any(_keeps(n, o) for n in new) and not any(_names(x, o) for x in dropped)]
        if left_out:
            return self._control_error(call, "This plan leaves out required outputs of your earlier plan: "
                                       + "; ".join(_label(o) for o in left_out) + ". Keep them, or list each under `dropped` with the reason "
                                       "(it is impossible, or the task did not ask for it). The previous plan is unchanged.")
        self.dropped += [x for x in dropped if any(_names(x, o) for o in self.obligations)]
        self.obligations = [o for o in self.obligations if not any(_names(x, o) for x in self.dropped)]
        self.obligations += [n for n in new if n["kind"] != "answer" and not n.get("optional")
                             and not any(_keeps(n, o) and _keeps(o, n) for o in self.obligations)]
        self.plan = plan.model_dump()
        self.plan_version += 1
        self.trace.write({"type": "plan", "step": self.steps, "version": self.plan_version, **self.plan, "obligations": self.obligations})
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
        if not raw or is_network_path(raw):
            return None  # a UNC/device/URL form is never this run's artifact; checked before any filesystem call
        arts = self.ctx.artifacts
        cand = Path(raw)
        for key in (str(cand), str(cand.resolve()) if cand.is_absolute() else "", str((self.ctx.artifacts_dir / cand.name).resolve()),
                    str((self.ctx.artifacts_dir.parent / cand).resolve())):
            if key and key in arts:
                return arts[key]
        return None

    async def _finish(self, call: ToolCall, *, others_in_turn: bool) -> dict:
        if call.parse_error:
            return self._control_error(call, f"Arguments are not valid JSON: {call.parse_error}.")
        try:
            a = FinishIn.model_validate(_without_nulls(call.arguments))
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
        for audio in [x for x in delivered if x.kind == "audio"]:
            # generated speech is always delivered with the text it reads, so the listener can read along
            for dep in audio.depends_on:
                script = self.ctx.artifacts.get(dep)
                if script is not None and script.kind in ("translation", "text") and script not in delivered and self._usable(script):
                    delivered.append(script)
                    self.trace.write({"type": "deliverable_added", "step": self.steps, "path": str(script.path), "reason": "text read by delivered audio"})
        unverified = [x.path.name for x in self.ctx.artifacts.values() if x.kind in VERIFIED_KINDS and x.verification is None]
        if unverified:
            reasons.append(f"generated translations/audio without verification: {unverified}; verify them (verify_translation/verify_speech)")
        failing = [x for x in delivered if self._effective_status(x) == "fail"]
        if status == "completed":
            if failing:
                reasons.append("deliverables failed verification (directly or through what they were made from): "
                               + "; ".join(x.path.name for x in failing) + ". Repair them differently, or finish with status partial and explain.")
            reasons += self._contract_gaps(delivered, answer)
            if not reasons:
                reasons += await self._coverage_gaps([x for x in delivered if self._usable(x)], answer)
        if reasons and self.finish_rejections < 2:
            self.finish_rejections += 1
            self.trace.write({"type": "finish_rejected", "step": self.steps, "reasons": reasons})
            return {"ok": False, "error": {"code": "finish_rejected", "message": "Cannot finish yet: " + " | ".join(reasons)}}
        limitations, assumptions = list(a.limitations), list(a.assumptions)
        if indic_fields:  # never relaxed: planner-written Indic text is removed (only that text)
            if not self._indic_ok(answer):  # keep the planner's English; remove only the text it wrote itself
                answer = self.ctx.provenance_guard.redact(answer)
            limitations = [x if self._indic_ok(x) else self.ctx.provenance_guard.redact(x) for x in limitations]
            assumptions = [x if self._indic_ok(x) else self.ctx.provenance_guard.redact(x) for x in assumptions]
        usable = [x for x in delivered if self._usable(x)]
        if reasons:  # accepted after repeated rejection: downgrade honestly
            limitations += [f"verification gate: {r}" for r in reasons]
            if status == "completed":
                status = "partial" if usable else "failed"
        limitations += [f"plan change: {_label(x)} from an earlier plan was dropped: {x['reason']}" for x in self.dropped]
        final = status
        if status == "completed" and (self.dropped or any(self._effective_status(x) == "warn" for x in usable)):
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
        gaps, required = [], []
        for spec in self.plan["deliverables"]:
            kind, lang, src = spec["kind"], spec.get("language"), (spec.get("source") or "").strip()
            label = f"{kind}{' (' + lang + ')' if lang else ''}{' from ' + src if src else ''}"                     f"{': ' + spec['description'][:60] if spec.get('description') else ''}"
            if kind == "answer":
                if not answer or not any(t["ok"] for t in self.tool_log):
                    gaps.append(f"declared deliverable '{label}' needs an answer based on at least one successful tool call")
                continue
            if not spec.get("optional"):  # optional outputs may be absent, so they never block finishing
                # a declared source must be in the artifact's input lineage
                fits = [str(x.path) for x in delivered if x.kind in KIND_MATCH[kind] and (not lang or x.language == lang)
                        and (not src or Path(src).name.lower() in {o.lower() for o in x.origin}) and self._usable(x)]
                required.append((label, fits, kind))
        # one artifact per declared deliverable: a maximum matching, so a general deliverable never takes the only
        # artifact that a more specific one could use while another artifact would have done for it
        owner: dict[str, int] = {}

        def assign(i: int, seen: set[str]) -> bool:
            for path in required[i][1]:
                if path not in seen:
                    seen.add(path)
                    if path not in owner or assign(owner[path], seen):
                        owner[path] = i
                        return True
            return False

        matched = {i for i in range(len(required)) if assign(i, set())}
        shared: set[tuple[str, str]] = set()  # (artifact, kind) already used by a sharing deliverable
        for i, (_, fits, kind) in enumerate(required):
            # A "text"/"transcript" deliverable declared next to the translation or subtitles that already is that text
            # (the same output under two kinds) may share its artifact, once per kind. Same-kind deliverables still need one each.
            if i in matched or kind not in ("text", "transcript"):
                continue
            p = next((p for p in fits if p in owner and required[owner[p]][2] != kind and (p, kind) not in shared), None)
            if p is not None:
                shared.add((p, kind))
                matched.add(i)
        gaps += [f"declared deliverable '{label}' has no matching verified artifact among the finish deliverables "
                 "(each declared deliverable needs its own artifact)" for i, (label, _, _) in enumerate(required) if i not in matched]
        return gaps

    async def _coverage_gaps(self, delivered: list[Artifact], answer: str) -> list[str]:
        """Before "completed": a fresh-context call to the planner model reads the original task and the delivered items
        and lists each requested output with the item that provides it. It sees neither the plan nor the conversation,
        so an output the plan never declared, or dropped on a replan, can still be found missing. It is the same model
        cross-checking itself, not an independent linguistic check: it can miss a gap or invent one. A check that cannot
        run, or lists nothing, does not confirm coverage, so "completed" is refused then too."""
        names = ["answer"] + [x.path.name for x in delivered]
        listing = [f"- answer: the reply text: \"{' '.join(answer.split())[:1500]}\""] + [
            f"- {x.path.name}: {x.kind}{', ' + LANGUAGE_NAMES.get(x.language, x.language) if x.language else ''}"
            f"{', made from ' + ', '.join(sorted({Path(o).name for o in x.origin})) if x.origin else ''}" for x in delivered]
        messages = [{"role": "system", "content": COVERAGE_PROMPT},
                    {"role": "user", "content": f"Task:\n{self.task}\n\nDelivered items:\n" + "\n".join(listing)}]
        tools = [COVERAGE_TOOL]
        est = self._llm_cost(prompt_token_bound(messages, tools), COVERAGE_COMPLETION_RESERVE)
        why, outputs, raw, t0 = None, None, None, time.monotonic()
        if est is not None and not self.ctx.ledger.can_afford(est):
            why = "not enough budget left for it"
        else:
            try:
                reply = await asyncio.wait_for(self.llm.chat(messages, tools, max_tokens=COVERAGE_COMPLETION_RESERVE if est is not None else None),
                                               timeout=max(0.0, min(self._time_left(), COVERAGE_TIMEOUT_S)))
            except (TimeoutError, LLMError) as exc:
                why = f"the planner call failed ({type(exc).__name__})"
                if est is not None and getattr(self.llm, "uncertain_attempts", 0):
                    self._charge_uncertain_llm(est, self.llm.uncertain_attempts, "coverage_check")
            else:
                cost = self._llm_cost(reply.prompt_tokens, reply.completion_tokens)
                self.ctx.ledger.add_llm(reply.prompt_tokens, reply.completion_tokens, cost)
                call = next((c for c in reply.tool_calls if c.name == "report_coverage"), None)
                raw = (call.raw_arguments if call else reply.content)[:500]
                try:
                    outputs = CoverageIn.model_validate(_without_nulls(call.arguments)).outputs if call and not call.parse_error else None
                except ValidationError:
                    outputs = None
                if outputs is None:
                    why = "the reply had no valid report_coverage call"
                elif not outputs:
                    why = "it listed no requested outputs"
        missing = [o.request for o in outputs or []
                   if not any(_item_name(c) in names for c in (o.covered_by if isinstance(o.covered_by, list) else [o.covered_by]))]
        self.trace.write({"type": "coverage_check", "step": self.steps, "latency_s": round(time.monotonic() - t0, 2),
                          "outputs": [o.model_dump() for o in outputs] if outputs is not None else None, "missing": missing, "unavailable": why,
                          **({"raw_reply": raw} if why else {})})
        if why:  # unconfirmed is not confirmed: "completed" needs the check, so call finish again or finish partial
            return [f"the check of the task against the delivered items could not run ({why}); call finish again to retry it, "
                    "or finish with status partial"]
        if missing:
            return ["an independent reading of the task found requested outputs with no matching delivered item: "
                    + "; ".join(self._safe(m) for m in missing) + ". Produce them and deliver them, or finish with status partial and say why"]
        return []

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
        inputs = {str(f) for f in self.files}
        for dep in art.depends_on:
            if dep in inputs:  # the user's own input file: nothing to verify
                continue
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
        if self.missing:  # checked in code before planning, so it is a fact even when the planner never ran
            answer += f" Also, these inputs do not exist: {', '.join(self.missing)}."
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
              "obligations": self.obligations, "dropped": self.dropped,
              "finish_rejections": self.finish_rejections, "budget_errors": self.budget_errors, "tool_log": self.tool_log,
              "messages": self.messages, "inventory": self.inventory, "pending": pending, "ledger": self.ctx.ledger.to_state(),
              "artifacts": [a.to_state() for a in self.ctx.artifacts.values()], "transcripts": {k: str(v) for k, v in self.ctx.transcripts.items()},
              "provenance": list(getattr(guard, "_corpus", [])), "elapsed_s": self.elapsed_before + (time.monotonic() - self.started),
              "finished": self.outcome is not None, "status": self.outcome.status if self.outcome else None}
        try:
            checkpoint.save(self.out, st)
        except OSError as exc:  # a checkpoint is best effort; the run itself continues
            self.trace.write({"type": "checkpoint_error", "error": repr(exc)})
        if self.outcome is None and getattr(self, "started_at", None) is not None:
            self.write_provisional()

    def write_provisional(self) -> None:
        """The report an interruption now would leave: replaced by the real one when the run ends. The checkpoint is
        not marked finished, so the run can still be resumed."""
        from .report import write_outputs

        try:
            how = (f"Continue it with: python -m bodhan_agent --resume --out \"{self.out}\"" if checkpoint.checkpoint_path(self.out).is_file()
                   else "It stopped before its first checkpoint: run the same command again.")
            self.forced_outcome("interrupted", f"The run was interrupted (stopped or killed) after step {self.steps}, or is still running. {how}")
            write_outputs(self, self.out, self.started_at, self.elapsed_before + (time.monotonic() - self.started), provisional=True)
        except Exception as exc:  # never let reporting break the run
            self.trace.write({"type": "report_error", "error": repr(exc)})
        finally:
            self.outcome = None

    def _restore(self, st: dict[str, Any]) -> dict | None:
        self.steps, self.plan, self.plan_version = st["steps"], st["plan"], st["plan_version"]
        self.obligations, self.dropped = st.get("obligations", []), st.get("dropped", [])
        self.finish_rejections, self.budget_errors = st["finish_rejections"], st["budget_errors"]
        self.tool_log, self.messages, self.inventory = st["tool_log"], st["messages"], st.get("inventory", {})
        self.missing, self._inputs_fp = st.get("missing", []), st.get("inputs", [])
        self.elapsed_before = float(st.get("elapsed_s", 0.0))
        self.ctx.ledger.load_state(st["ledger"])
        rebuilt = checkpoint.ledger_from_trace(self.out / "trace.jsonl") if self.out else None
        if rebuilt is not None:
            # The trace is written as each request settles, so it is normally the more complete record; if it was
            # damaged, the checkpoint still holds. Both under-count at worst, so keep the larger of each amount.
            saved = self.ctx.ledger.to_state()
            merged = {k: (max(saved.get(k) or 0, v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in rebuilt.items()}
            merged["by_model"] = {m: max(saved.get("by_model", {}).get(m, 0.0), rebuilt.get("by_model", {}).get(m, 0.0))
                                  for m in set(saved.get("by_model", {})) | set(rebuilt.get("by_model", {}))}
            merged["llm_priced"] = bool(saved.get("llm_priced") or rebuilt.get("llm_priced"))
            self.ctx.ledger.load_state(merged)
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

    def _compact(self, level: int = 0) -> bool:
        """Keep the conversation inside small planner context windows: older tool results are replaced by a short
        record (ok, error code, paths, verification status). Artifacts stay on disk and read_text can reopen them.
        level 0: only past CONTEXT_BYTES, keep the last 6 results; after a "too long" rejection: 1 keeps the last 2,
        2 keeps the last 1 shortened, 3 shortens every result (some endpoints cap a request at ~8k tokens)."""
        size = len(json.dumps(self.messages, ensure_ascii=False).encode("utf-8"))
        if size <= CONTEXT_BYTES and not level:
            return False
        tool_msgs = [i for i, m in enumerate(self.messages) if m.get("role") == "tool"]
        keep = set(tool_msgs[-{0: KEEP_RECENT_TOOL_RESULTS, 1: 2, 2: 1}.get(level, 0):]) if level < 3 else set()
        changed = False
        for i in tool_msgs:
            content = self.messages[i].get("content") or ""
            if i in keep and level == 2 and len(content) > 1500:  # the newest result, shortened but still readable
                self._forget_page(content)
                self.messages[i] = {**self.messages[i], "content": content[:1500] + '... [shortened to fit the planner context; use read_text]"'}
                changed = True
                continue
            if i in keep or content.startswith('{"compacted"'):
                continue
            try:
                r = json.loads(content)
            except ValueError:
                r = {}
            self._forget_page(content)
            small: dict[str, Any] = {"compacted": True, "ok": r.get("ok")}
            for k in ("path", "transcript_path", "subtitles_path", "next_offset", "target_language", "language", "duration_s"):
                if r.get(k) is not None:
                    small[k] = r[k]
            if isinstance(r.get("error"), dict):
                small["error"] = r["error"].get("code")
            if isinstance(r.get("verification"), dict):
                small["verification"] = r["verification"].get("status")
            small["note"] = "older result shortened to save context; open artifacts with read_text if needed"
            self.messages[i] = {**self.messages[i], "content": json.dumps(small, ensure_ascii=False)}
            changed = True
        if level >= 3:  # also the planner's own earlier arguments (e.g. a long write_text content), kept valid JSON
            last_assistant = max((i for i, m in enumerate(self.messages) if m.get("role") == "assistant"), default=-1)
            for i, m in enumerate(self.messages):
                if m.get("role") != "assistant" or i == last_assistant or not m.get("tool_calls"):
                    continue
                calls = []
                for c in m["tool_calls"]:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    if isinstance(args, dict) and len(c["function"].get("arguments") or "") > 400:
                        args = {k: (v if len(json.dumps(v, ensure_ascii=False)) <= 120 else "[shortened to save context]") for k, v in args.items()}
                        c = {**c, "function": {**c["function"], "arguments": json.dumps(args, ensure_ascii=False)}}
                        changed = True
                    calls.append(c)
                self.messages[i] = {**m, "tool_calls": calls}
        return changed

    def _forget_page(self, content: str) -> None:
        """A read_text page that is shortened may never reach the planner whole: it no longer counts as read
        (write_text's source_paths then needs the file to be read again)."""
        try:
            r = json.loads(content)
        except ValueError:
            return
        if isinstance(r, dict) and r.get("ok") and {"path", "offset", "returned_chars", "total_chars"} <= set(r):
            self.ctx.forget_read(r["path"], r["offset"], r["offset"] + r["returned_chars"])

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
