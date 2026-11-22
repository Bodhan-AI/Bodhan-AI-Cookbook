"""The task agent: preflight, plan, act through MCP tools, verify, retry once, stop, report.

#Plan
- Preflight in plain code (inputs, unsupported languages, duration target) ends impossible tasks before any
  paid call.
- The LLM writes a JSON plan (assumptions, steps, deliverables), then drives tools through an in-process MCP
  client session against the same server that `python -m bodhan_mcp` runs over stdio.
- Hard budgets in code: tool steps, rupees (enforced inside every Bodhan call by the shared ledger), wall time
  and per-call timeouts. The LLM cannot override them.
- finish triggers deterministic checks plus an LLM judge; failures go back to the LLM for one targeted redo.
- Status comes from the checks. Everything is written to trace.jsonl as it happens; result.md and report.json at
  the end, even when the run fails part way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

from bodhan_mcp import server as mcp_server
from bodhan_mcp.config import Settings
from bodhan_mcp.langs import has_indic
from bodhan_mcp.tools import ToolContext

from . import prompts
from .guard import compact_result, indic_violations, redact, wrap_untrusted
from .llm import LLMError, extract_json
from .preflight import Preflight, run_preflight
from .trace import Trace
from .verify import Verification, verify_artifacts

log = logging.getLogger("bodhan_agent")

FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Call once the task is done (or done as far as possible). Deliverables are checked automatically before the run ends.",
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "English answer for the user: what was done, results, any answer to a question. No Indian-language text; refer to files by path."},
                "artifacts": {"type": "array", "description": "Deliverable files (paths from tool results).",
                              "items": {"type": "object", "properties": {"path": {"type": "string"}, "description": {"type": "string"}},
                                        "required": ["path", "description"]}},
                "assumptions": {"type": "array", "items": {"type": "string"},
                                "description": "Choices you made where the task was ambiguous (language, voice, length, format)."},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["answer", "artifacts"],
        },
    },
}
GIVE_UP_TOOL = {
    "type": "function",
    "function": {
        "name": "give_up",
        "description": "Stop because the task cannot be done. Produces no output files. Explain why and what is possible instead.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "category": {"type": "string", "enum": ["unsupported_language", "missing_input", "unsupported_capability", "budget", "other"]},
                "suggestions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["reason", "category"],
        },
    },
}
CONTROL = {"finish", "give_up"}
READ_ONLY_TOOLS = {"read_text", "inspect_file"}
OUTPUT_KEYS = ("output_path", "audio_path", "transcript_path", "segments_path", "srt_path", "path", "details_path")


@dataclass
class RunConfig:
    task: str
    files: str | None
    out: Path
    max_steps: int = 30
    max_cost_inr: float = 20.0
    max_minutes: float = 20.0
    tool_timeout_s: float = 300.0
    judge: bool = True
    max_redos: int = 1


@dataclass
class RunState:
    status: str = "running"
    answer: str = ""
    artifacts: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    plan: dict | None = None
    verification: Verification | None = None
    judge: dict | None = None
    steps: int = 0
    model_steps: int = 0
    redos: int = 0


class Agent:
    def __init__(self, cfg: RunConfig, llm: Any, ctx: ToolContext | None = None):
        self.cfg = cfg
        self.llm = llm
        self.out = cfg.out.resolve()
        self.work = self.out / "work"
        self.art_dir = self.out / "artifacts"
        for d in (self.work, self.art_dir):  # only our own subfolders: a rerun must not mix in old outputs
            if d.is_dir():
                shutil.rmtree(d)
        for d in (self.out, self.work, self.art_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.trace = Trace(self.out / "trace.jsonl")
        self.state = RunState()
        self.records: dict[str, dict] = {}
        self.done_calls: dict[str, str] = {}
        self._ctx = ctx
        self.t0 = time.time()
        self._short_results: dict[str, str] = {}
        self._last_summary: dict[str, str] = {}
        self._keep_full = 3  # most recent tool results sent in full; older ones as one-line summaries
        self.used_inputs: set[str] = set()

    # ------------------------------------------------------------------ entry
    async def run(self) -> dict:
        cfg, st = self.cfg, self.state
        pf = run_preflight(cfg.task, cfg.files, cfg.max_cost_inr)
        self.pf = pf
        self.trace.log("preflight", "checks", files=[str(f) for f in pf.files][:50], missing=pf.missing,
                       languages=pf.supported_mentioned, unsupported=pf.unsupported_mentioned,
                       duration_target_s=pf.duration_target_s, impossible=pf.impossible_reason, notes=pf.notes)
        self.ctx = self._ctx or ToolContext(Settings(output_dir=self.work, allowed_dirs=[pf.input_root or Path.cwd(), self.out],
                                                     max_cost_inr=cfg.max_cost_inr))
        self.ctx.settings.output_dir = self.work
        self.ctx.workspace.output_dir = self.work.resolve()
        self.ctx.settings.allowed_dirs = [pf.input_root or Path.cwd(), self.out]
        self.ctx.ledger.max_cost_inr = cfg.max_cost_inr
        mcp_server.set_context(self.ctx)
        if pf.impossible_reason:
            st.status, st.answer = "impossible", pf.impossible_reason
            self.trace.log("stop", "impossible", reason=pf.impossible_reason, source="preflight")
            return await self._finalize()
        try:
            async with create_connected_server_and_client_session(mcp_server.mcp._mcp_server) as session:
                self.session = session
                listed = await session.list_tools()
                # The LLM sees short tool descriptions to keep every request small (some providers cap tokens per
                # minute at 8K); third-party MCP clients still get the full descriptions from the server.
                self.tools = [{"type": "function", "function": {"name": t.name, "description": _short(t.description or "", 260),
                                                                 "parameters": _compact_schema(t.inputSchema)}} for t in listed.tools]
                self.tools += [FINISH_TOOL, GIVE_UP_TOOL]
                facts = await self._describe_inputs(pf)
                await self._plan(facts)
                if st.status == "running":
                    await self._loop(facts)
        except Exception as exc:  # report, never crash without outputs
            inner = _unwrap(exc)
            if isinstance(inner, LLMError):
                st.errors.append(str(inner))
                self.trace.log("stop", "llm_error", error=str(inner))
                self._salvage("The LLM became unavailable before the agent called finish.")
            else:
                log.exception("run failed")
                st.errors.append(f"{inner.__class__.__name__}: {inner}")
                st.status = "failed"
                self.trace.log("stop", "crash", error=f"{inner.__class__.__name__}: {inner}")
        finally:
            await self.ctx.client.aclose()
        return await self._finalize()

    # ------------------------------------------------------------------ inputs and plan
    async def _describe_inputs(self, pf: Preflight) -> str:
        lines = []
        for f in pf.files[:25]:
            res = await self.session.call_tool("inspect_file", {"path": str(f)})
            info = res.structuredContent or {}
            info = {k: v for k, v in info.items() if v not in (None, [], {})}
            self.trace.log("inputs", "tool", tool="inspect_file", args={"path": str(f)}, ok=not res.isError, result_summary=info)
            text, hits = wrap_untrusted("inspect_file", info)
            if hits:
                self.state.warnings.append(f"Instruction-like text in {f.name} was treated as data and ignored: {hits}")
            lines.append(f"- {f}\n  {text}")
        if len(pf.files) > 25:
            lines.append(f"- ... and {len(pf.files) - 25} more files (use inspect_file on the folder).")
        if not pf.files:
            lines.append("- (no input files)")
        notes = "\n".join(f"Note: {n}" for n in pf.notes)
        return "\n".join(lines) + ("\n" + notes if notes else "")

    def _task_block(self, facts: str) -> str:
        return (f"TASK: {self.cfg.task}\n\nINPUT FILES (pass these paths to tools):\n{facts}\n\n"
                f"BUDGET: at most {self.cfg.max_steps} tool calls and ₹{self.cfg.max_cost_inr:.2f} of Bodhan usage. "
                f"Tool outputs are written to {self.work}.")

    async def _plan(self, facts: str) -> None:
        catalogue = "\n".join(f"- {t['function']['name']}: {_short(t['function']['description'], 160)}" for t in self.tools)
        msgs = [{"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": self._task_block(facts) + f"\n\nAVAILABLE TOOLS:\n{catalogue}\n\n" + prompts.PLANNER}]
        plan = None
        for attempt in range(2):
            reply = await self.llm.chat(msgs)
            plan = extract_json(reply.content)
            self.trace.log("plan", "llm", attempt=attempt + 1, ok=plan is not None, plan=plan or reply.content[:500])
            if plan:
                break
            msgs += [{"role": "assistant", "content": reply.content}, {"role": "user", "content": "Return only the JSON object."}]
        if not plan:
            plan = {"feasible": True, "assumptions": [], "steps": [], "note": "planner returned no JSON; acting without a written plan"}
            self.state.warnings.append("The planner did not return a usable plan; the agent acted without one.")
        self.state.plan = plan
        self.state.assumptions += [a for a in plan.get("assumptions", []) if isinstance(a, str)]
        if plan.get("feasible") is False:
            self.state.status = "impossible"
            self.state.answer = plan.get("infeasible_reason") or "The planner judged the task impossible with the available tools."
            self.trace.log("stop", "impossible", reason=self.state.answer, source="planner")

    # ------------------------------------------------------------------ act loop
    async def _loop(self, facts: str) -> None:
        cfg, st = self.cfg, self.state
        plan_text = json.dumps(st.plan, ensure_ascii=False, indent=1)
        messages: list[dict] = [
            {"role": "system", "content": prompts.SYSTEM},
            {"role": "user", "content": self._task_block(facts) + f"\n\nYOUR PLAN:\n{plan_text}\n\nCarry it out now with tool calls."},
        ]
        nudges = 0
        while st.status == "running":
            if st.steps >= cfg.max_steps:
                return self._stop_budget(f"Reached the step limit ({cfg.max_steps}).")
            if (time.time() - self.t0) / 60 > cfg.max_minutes:
                return self._stop_budget(f"Reached the time limit ({cfg.max_minutes:.0f} min).")
            if self.ctx.ledger.remaining is not None and self.ctx.ledger.remaining < 0.001:
                return self._stop_budget(f"Spent the whole ₹{cfg.max_cost_inr:.2f} budget.")
            left = cfg.max_steps - st.steps
            if left <= 4 and not getattr(self, "_warned_steps", False):
                self._warned_steps = True
                messages.append({"role": "user", "content": f"Only {left} tool calls are left. Stop refining: call finish now "
                                 "with the best outputs you already have, and list anything unfinished under limitations."})
            try:
                reply = await self.llm.chat(self._context(messages), self.tools)
            except LLMError as exc:
                if not exc.too_large or self._keep_full <= 0:
                    raise
                self._keep_full = 0  # shrink every earlier tool result to its summary and try once more
                self.trace.log("act", "context_compacted", reason="request too large for the LLM provider")
                reply = await self.llm.chat(self._context(messages), self.tools)
            st.model_steps += 1
            self.trace.log("act", "llm", reason=reply.content[:500], tool_calls=[c.name for c in reply.tool_calls],
                           prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens)
            if not reply.tool_calls:
                nudges += 1
                messages.append({"role": "assistant", "content": reply.content or "(no reply)"})
                if nudges > 2:
                    st.status, st.answer = "partial", reply.content or "The model stopped without calling finish."
                    st.limitations.append("The model stopped without calling finish; outputs were not verified.")
                    return
                messages.append({"role": "user", "content": "Continue with a tool call, or call finish (or give_up) if you are done."})
                continue
            nudges = 0
            messages.append({"role": "assistant", "content": reply.content or None,
                             "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                                            for c in reply.tool_calls]})
            normal = [c for c in reply.tool_calls if c.name not in CONTROL]
            control = next((c for c in reply.tool_calls if c.name in CONTROL), None)
            contents = await asyncio.gather(*(self._exec(c, reply.content) for c in normal))
            for c, content in zip(normal, contents, strict=True):
                messages.append({"role": "tool", "tool_call_id": c.id, "content": content})
                self._short_results[c.id] = self._last_summary.get(c.id, content[:400])
            for c in reply.tool_calls:
                if c.name in CONTROL and c is not control:
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": "Ignored: only one finish/give_up per turn."})
            if control is None:
                continue
            args = _parse_args(control.arguments)
            if control.name == "give_up":
                gargs = args if isinstance(args, dict) else {"reason": str(args), "category": "other"}
                if self._should_challenge(gargs):
                    self._challenged = True
                    self.trace.log("act", "give_up_challenged", **gargs)
                    messages.append({"role": "tool", "tool_call_id": control.id, "content": (
                        "Before giving up, check once: the inputs exist and the languages are supported. Is the missing piece "
                        "something you can do yourself in English (reading, summarising, explaining, choosing) or by "
                        "chaining the available tools? If so, do it now. If the task truly cannot be done, call give_up again.")})
                    continue
                messages.append({"role": "tool", "tool_call_id": control.id, "content": "Stopping."})
                self._give_up(gargs)
                return
            feedback = await self._finish(args if isinstance(args, dict) else {})
            if feedback is None:
                return
            messages.append({"role": "tool", "tool_call_id": control.id, "content": feedback})

    def _context(self, messages: list[dict]) -> list[dict]:
        """What the LLM is sent: the full history, but older tool results replaced by their short summaries."""
        tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
        keep = set(tool_idx[-self._keep_full:]) if self._keep_full > 0 else set()
        out = []
        for i, m in enumerate(messages):
            if m["role"] == "tool" and i not in keep and m.get("tool_call_id") in self._short_results:
                out.append({**m, "content": "(earlier result, summarised) " + self._short_results[m["tool_call_id"]]})
            else:
                out.append(m)
        return out

    async def _exec(self, call, reason: str) -> str:
        st = self.state
        args = _parse_args(call.arguments)
        if not isinstance(args, dict):
            return _err("invalid_argument", f"Arguments for {call.name} are not valid JSON: {args}")
        bad = indic_violations(args)
        if bad:
            self.trace.log("act", "refused", tool=call.name, args=args, why="indic_text_from_llm", fields=bad)
            return _err("invalid_argument", f"Refused: argument(s) {bad} contain Indian-language text written by you. "
                        "You may only write English. Pass Indian-language text by `path` to a file produced by a tool, "
                        "or write English and translate it with translate_text.")
        if call.name == "synthesize_speech" and args.get("verify") is False:
            args["verify"] = True  # the agent may not switch off its own checks
            self.trace.log("act", "verify_forced", tool=call.name, why="LLM asked for verify=false; speech is always back-transcribed")
        key = f"{call.name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if key in self.done_calls:
            self.trace.log("act", "duplicate_skipped", tool=call.name, args=args)
            return "Already done earlier with the same arguments; reuse that result:\n" + self.done_calls[key]
        if st.steps >= self.cfg.max_steps:
            return _err("budget_exceeded", "Step limit reached; call finish with what you have.")
        st.steps += 1
        spent0 = self.ctx.ledger.spent
        t0 = time.perf_counter()
        try:
            res = await asyncio.wait_for(self.session.call_tool(call.name, args), timeout=self.cfg.tool_timeout_s)
        except TimeoutError:
            self.trace.log("act", "tool", tool=call.name, args=args, ok=False, error="timeout",
                           latency_ms=int((time.perf_counter() - t0) * 1000))
            return _err("upstream_timeout", f"{call.name} took longer than {self.cfg.tool_timeout_s:.0f}s and was stopped.")
        latency = int((time.perf_counter() - t0) * 1000)
        if res.isError:
            text = " ".join(getattr(c, "text", "") for c in res.content)
            body = text[text.find("{"):] if "{" in text else text
            self.trace.log("act", "tool", tool=call.name, args=args, ok=False, error=body[:600], latency_ms=latency, reason=reason[:300],
                           cost_inr=round(self.ctx.ledger.spent - spent0, 4), cumulative_cost_inr=round(self.ctx.ledger.spent, 4))
            return body
        result = res.structuredContent or {}
        if "result" in result and len(result) == 1 and isinstance(result["result"], dict):
            result = result["result"]
        for k in OUTPUT_KEYS if call.name not in READ_ONLY_TOOLS else ():  # a read must not replace who produced a file
            v = result.get(k)
            if isinstance(v, str) and v:
                p = Path(v) if Path(v).is_absolute() else (Path.cwd() / v)
                self.records[str(p.resolve())] = {"tool": call.name, "result": result, "args": args}
        cost = result.get("cost_inr", round(self.ctx.ledger.spent - spent0, 4))
        self.trace.log("act", "tool", tool=call.name, args=args, ok=True, latency_ms=latency, reason=reason[:300],
                       result_summary=_summary(result), cost_inr=cost, cumulative_cost_inr=round(self.ctx.ledger.spent, 4))
        for k in ("path", "segments_path"):
            if isinstance(args.get(k), str):
                self.used_inputs.add(Path(args[k]).name)
        self._last_summary[call.id] = json.dumps({"tool": call.name, **_summary(result)}, ensure_ascii=False)[:600]
        content, hits = wrap_untrusted(call.name, compact_result(result))
        if hits:
            msg = f"Instruction-like text in {call.name} output was treated as data and ignored: {hits}"
            if msg not in st.warnings:
                st.warnings.append(msg)
        self.done_calls[key] = content
        return content

    # ------------------------------------------------------------------ control tools
    def _should_challenge(self, args: dict) -> bool:
        """A give-up for a missing capability or input, when preflight found the inputs and languages fine, gets one
        second look: models sometimes miss that they can do the step themselves. Budget and language refusals do not."""
        return (not getattr(self, "_challenged", False) and self.pf.impossible_reason is None
                and args.get("category") in ("unsupported_capability", "missing_input", "other"))

    def _give_up(self, args: dict) -> None:
        st = self.state
        st.status = "impossible" if args.get("category") != "budget" else "budget_exhausted"
        st.answer = args.get("reason", "The task cannot be done with the available tools.")
        sugg = [s for s in args.get("suggestions", []) if isinstance(s, str)]
        if sugg:
            st.answer += "\n\nWhat is possible instead:\n" + "\n".join(f"- {s}" for s in sugg)
        self.trace.log("stop", "give_up", **args)

    async def _finish(self, args: dict) -> str | None:
        """Verify a finish call. Returns feedback for the model when a redo is wanted, None when the run ends."""
        st = self.state
        answer = str(args.get("answer", "")).strip()
        raw_arts = args.get("artifacts") or []
        arts = []
        for a in raw_arts if isinstance(raw_arts, list) else []:
            if isinstance(a, str):
                a = {"path": a, "description": ""}
            if isinstance(a, dict) and a.get("path"):
                arts.append({"path": str(a["path"]), "description": str(a.get("description", ""))})
        problems: list[str] = []
        answer_problem = None
        if has_indic(answer):
            answer_problem = "Your answer contains Indian-language text. Rewrite it in English and refer to files by path."
        resolved = []
        for a in arts:
            p = self._resolve(a["path"])
            if p is None:
                problems.append(f"Artifact {a['path']} does not exist.")
            else:
                resolved.append((p, a))
        skipped = [f.name for f in self.pf.files if f.name not in self.used_inputs
                   and not re.search(rf"(?<![\w.-]){re.escape(f.stem)}(?![\w-])", answer)][:10]
        if skipped and len(self.pf.files) <= 40:
            problems.append(f"These input files were never processed or mentioned: {', '.join(skipped)}. "
                            "Process them, or say in the answer why they were left out.")
        targets = [t for t in (st.plan or {}).get("target_languages", []) if isinstance(t, str)]
        ver = verify_artifacts([p for p, _ in resolved], self.records, targets, self.pf.duration_target_s)
        problems += [f"{c.artifact}: {c.name} failed ({c.detail})" for c in ver.failures()]
        judge = await self._judge(answer, resolved, ver) if self.cfg.judge else None
        if judge:  # wording inside Indian-language text is not something the agent may fix; keep it for a human
            jp = [p for p in judge.get("problems", []) if isinstance(p, str)]
            judge["notes"] = [n for n in judge.get("notes", []) if isinstance(n, str)] + [p for p in jp if has_indic(p)]
            judge["problems"] = [p for p in jp if not has_indic(p)]
            if judge.get("satisfied") is False:
                problems += [f"Reviewer: {p}" for p in judge["problems"]]
        notes = [n for n in (judge or {}).get("notes", []) if isinstance(n, str)]
        self.trace.log("verify", "checks", passed=ver.passed, checks=ver.as_list(), judge=judge, problems=problems,
                       answer_problem=answer_problem, notes=notes)
        can_redo = st.redos < self.cfg.max_redos and st.steps < self.cfg.max_steps and (
            self.ctx.ledger.remaining is None or self.ctx.ledger.remaining > 0.01)
        if (problems or answer_problem) and can_redo:
            st.redos += 1
            todo = problems + ([answer_problem] if answer_problem else [])
            return ("Verification found problems. Fix them (one more attempt), then call finish again. "
                    "If a problem cannot be fixed, call finish and list it under limitations.\n- " + "\n- ".join(todo))
        st.limitations += [f"Reviewer note (not checked by code): {n}" for n in notes]
        if answer_problem:  # the files decide the status; an answer we cannot show does not
            st.limitations.append("The model's final answer quoted Indian-language text, so it was withheld; the files are unaffected.")
        st.answer = answer if not has_indic(answer) else "(The model's answer contained Indian-language text and was withheld; see the files.)"
        final_assumptions = [x for x in args.get("assumptions", []) if isinstance(x, str) and x.strip()]
        if final_assumptions:  # the finish call states what was actually assumed; it supersedes the plan's guesses
            st.assumptions = list(dict.fromkeys(final_assumptions))
        st.limitations += [x for x in args.get("limitations", []) if isinstance(x, str)]
        st.verification, st.judge = ver, judge
        st.artifacts = [{"source": str(p), "description": a["description"]} for p, a in resolved]
        st.status = "completed" if not problems else "partial"
        if problems:
            st.limitations += [f"Unresolved after verification: {p}" for p in problems]
        self.trace.log("stop", "finish", status=st.status, artifacts=[a["path"] for a in arts])
        return None

    async def _judge(self, answer: str, resolved: list, ver: Verification) -> dict | None:
        items = []
        for p, a in resolved:
            entry: dict[str, Any] = {"file": p.name, "description": a["description"]}
            rec = self.records.get(str(p.resolve()), {})
            res = rec.get("result", {})
            for k in ("duration_s", "language", "target_language", "voice", "cues", "warnings", "missing_numbers", "page_count", "flags"):
                if res.get(k) not in (None, [], ""):
                    entry[k] = res[k]
            if p.suffix.lower() in (".md", ".txt", ".srt"):
                entry["preview"] = p.read_text(encoding="utf-8", errors="ignore")[:700]
            items.append(entry)
        payload = {"task": self.cfg.task, "duration_target_s": self.pf.duration_target_s, "answer": answer[:3000],
                   "deliverables": items, "automatic_checks": ver.as_list()}
        msgs = [{"role": "system", "content": prompts.JUDGE},
                {"role": "user", "content": wrap_untrusted("deliverables", payload)[0]}]
        try:
            reply = await self.llm.chat(msgs)
        except LLMError as exc:
            self.trace.log("verify", "judge_error", error=str(exc))
            return None
        verdict = extract_json(reply.content)
        self.trace.log("verify", "judge", verdict=verdict or reply.content[:300])
        return verdict if isinstance(verdict, dict) and "satisfied" in verdict else None

    def _salvage(self, why: str) -> None:
        """Run stopped before finish: hand over the outputs already made, checked, and labelled honestly."""
        st = self.state
        finals: dict[str, Path] = {}
        for rec in self.records.values():
            res, tool = rec["result"], rec["tool"]
            key = {"synthesize_speech": "audio_path", "write_subtitles": "srt_path", "translate_text": "output_path"}.get(tool)
            if key and res.get(key):
                p = self._resolve(res[key])
                if p:
                    finals[str(p)] = p
        if not finals:
            st.status = "failed"
            return
        paths = list(finals.values())
        targets = [t for t in (st.plan or {}).get("target_languages", []) if isinstance(t, str)]
        st.verification = verify_artifacts(paths, self.records, targets, self.pf.duration_target_s)
        st.artifacts = [{"source": str(p), "description": "Produced before the run stopped; checked by code only"} for p in paths]
        st.status = "partial"
        st.answer = (f"{why} The files below were already produced by the tools and are delivered with their automatic "
                     "checks, but the task was not confirmed complete.")
        st.limitations.append(why)

    def _stop_budget(self, why: str) -> None:
        st = self.state
        self.trace.log("stop", "budget", reason=why)
        self._salvage(f"Stopped before finishing: {why}")  # hand over what was already made, checked
        if st.status == "failed":  # nothing was produced
            st.status = "budget_exhausted"
            st.answer = f"Stopped: {why} No output was finished before the stop."
            st.limitations.append(why)

    def _resolve(self, path: str) -> Path | None:
        p = Path(path)
        roots = [Path.cwd(), self.work, self.out] + ([self.pf.input_root] if self.pf.input_root else [])
        cands = [p] if p.is_absolute() else [r / p for r in roots]
        for c in cands:
            if c.exists() and c.is_file():
                return c.resolve()
        return None

    # ------------------------------------------------------------------ outputs
    async def _finalize(self) -> dict:
        st = self.state
        delivered = []
        if st.status not in ("impossible",):
            for a in st.artifacts:
                src = Path(a["source"])
                dst = self.art_dir / src.name
                n = 2
                while dst.exists() and dst.resolve() != src.resolve():
                    dst = self.art_dir / f"{src.stem}_{n}{src.suffix}"
                    n += 1
                if src.resolve() != dst.resolve():
                    shutil.copy2(src, dst)
                delivered.append({"path": f"artifacts/{dst.name}", "description": a["description"],
                                  "ai_generated_audio": dst.suffix.lower() in (".mp3", ".wav")})
        elapsed = round(time.time() - self.t0, 2)
        ledger = self.ctx.ledger.summary() if hasattr(self, "ctx") else {"total_inr": 0.0}
        model_tools = [e for e in self.trace.events if e.get("kind") == "tool" and e.get("tool") not in
                       ("inspect_file", "read_text", "write_text", "write_subtitles")]
        latency_target = 30 if len(model_tools) <= 1 else 300
        cache_hits = sum(m.get("cache_hits", 0) for m in ledger.get("by_model", {}).values())
        ver = st.verification
        report = {
            "status": st.status,
            "task": self.cfg.task,
            "files": [str(f) for f in self.pf.files] if hasattr(self, "pf") else [],
            "started_at": datetime.fromtimestamp(self.t0, tz=UTC).isoformat(),
            "wall_time_s": elapsed,
            "steps": {"tool_calls": st.steps, "llm_turns": st.model_steps, "max_steps": self.cfg.max_steps, "redos": st.redos},
            "cost": ledger,
            "llm": self.llm.usage() if hasattr(self.llm, "usage") else {},
            "plan": st.plan,
            "assumptions": st.assumptions,
            "artifacts": delivered,
            "verification": {"passed": ver.passed if ver else None, "checks": ver.as_list() if ver else [], "judge": st.judge},
            "warnings": st.warnings,
            "errors": st.errors,
            "limitations": st.limitations,
            "targets": {
                "budget": {"target": f"<= ₹{self.cfg.max_cost_inr:.2f}", "actual_inr": ledger.get("total_inr", 0.0),
                           "met": ledger.get("total_inr", 0.0) <= self.cfg.max_cost_inr + 1e-9},
                "latency": {"target_s": latency_target, "actual_s": elapsed, "met": elapsed <= latency_target,
                            "kind": "single-step" if latency_target == 30 else "multi-step"},
                "no_waste": {"identical_requests_resent": 0, "served_from_cache": cache_hits,
                             "note": "Every Bodhan request is keyed by its exact input; repeats are answered from cache, never re-sent."},
                "verification": {"met": bool(ver and ver.passed) if st.status in ("completed", "partial") else None,
                                 "note": "audio back-transcribed, translations checked for numbers, script and truncation"},
                "honest_failure": {"met": st.status != "impossible" or not delivered},
            },
        }
        missed = [k for k, v in report["targets"].items() if v.get("met") is False]
        report["targets_missed"] = missed
        (self.out / "report.json").write_text(json.dumps(redact(report, 4000), ensure_ascii=False, indent=2), encoding="utf-8")
        (self.out / "result.md").write_text(render_result(self.cfg, st, delivered, report), encoding="utf-8")
        self.trace.log("end", "summary", status=st.status, cost_inr=ledger.get("total_inr"), wall_time_s=elapsed)
        return report


def _short(text: str, limit: int) -> str:
    """First sentences of a description, up to `limit` characters."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind(". ") + 1] if ". " in cut else cut.rsplit(" ", 1)[0] + " ..."


def _compact_schema(schema: dict) -> dict:
    """Input schema with shorter parameter descriptions, no titles, null defaults or `anyOf [X, null]` wrappers.
    Same meaning for the LLM, roughly half the tokens."""
    if not isinstance(schema, dict):
        return schema
    variants = schema.get("anyOf")
    if isinstance(variants, list):
        real = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
        if len(real) == 1 and isinstance(real[0], dict):
            schema = {**{k: v for k, v in schema.items() if k != "anyOf"}, **real[0]}
    out = {}
    for k, v in schema.items():
        if k == "title" or (k == "default" and v is None):
            continue
        if k == "description" and isinstance(v, str):
            out[k] = _short(v, 120)
        elif isinstance(v, dict):
            out[k] = _compact_schema(v)
        elif isinstance(v, list):
            out[k] = [_compact_schema(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out


def _unwrap(exc: BaseException) -> BaseException:
    """The MCP session runs in a task group, which wraps errors in exception groups; find the real one."""
    while hasattr(exc, "exceptions") and getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]
    return exc


def _parse_args(raw: str) -> Any:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return f"{exc.msg} at position {exc.pos}"


def _err(code: str, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}})


def _summary(result: dict) -> dict:
    keep = ("output_path", "audio_path", "transcript_path", "segments_path", "srt_path", "language", "target_language",
            "duration_s", "chars", "page_count", "cues", "voice", "warnings", "flags", "missing_numbers", "kind", "notes")
    out = {k: result[k] for k in keep if result.get(k) not in (None, [], "")}
    ver = result.get("verification")
    if isinstance(ver, dict):
        out["verification"] = {k: ver.get(k) for k in ("checked", "mean_cer", "max_cer", "chunks_flagged", "chunks_retried")}
    return out


def render_result(cfg: RunConfig, st: RunState, delivered: list[dict], report: dict) -> str:
    cost = report["cost"].get("total_inr", 0.0)
    lines = [
        "# Task result", "",
        f"**Task:** {cfg.task}", "",
        f"**Status:** {st.status}  ",
        f"**Cost:** ₹{cost:.3f} of ₹{cfg.max_cost_inr:.2f} budget · **Time:** {report['wall_time_s']:.0f} s · "
        f"**Tool calls:** {st.steps} of {cfg.max_steps}", "",
        "## Answer", "", st.answer or "(no answer)", "",
    ]
    if delivered:
        lines += ["## Artifacts", "", "| File | What it is |", "| --- | --- |"]
        for d in delivered:
            label = " (AI-generated speech, not a real person)" if d["ai_generated_audio"] else ""
            lines.append(f"| [{d['path']}]({d['path']}) | {d['description']}{label} |")
        lines.append("")
    elif st.status == "impossible":
        lines += ["No output files were produced, because the task cannot be done as asked.", ""]
    if st.assumptions:
        lines += ["## Assumptions", ""] + [f"- {a}" for a in st.assumptions] + [""]
    if st.verification and st.verification.checks:
        lines += ["## Verification", "", "| Artifact | Check | Result | Detail |", "| --- | --- | --- | --- |"]
        for c in st.verification.checks:
            lines.append(f"| {c.artifact} | {c.name} | {'pass' if c.passed else 'FAIL'} | {c.detail} |")
        if st.judge:
            lines.append(f"\nReviewer (LLM judge): {'satisfied' if st.judge.get('satisfied') else 'not satisfied'}"
                         + (f"; {'; '.join(st.judge.get('problems', []))}" if st.judge.get("problems") else ""))
        lines.append("")
    if st.limitations or st.errors:
        lines += ["## Limitations and what was not done", ""] + [f"- {x}" for x in st.limitations + st.errors] + [""]
    if st.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in st.warnings] + [""]
    lines += ["---", "Generated by bodhan_agent. Speech, transcription, translation and OCR all come from Bodhan models; "
              "see trace.jsonl for every step and report.json for costs and checks."]
    return "\n".join(lines) + "\n"
