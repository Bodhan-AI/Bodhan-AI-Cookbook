"""The act loop: the LLM picks tools, code guards and executes them, results come back compacted.

Budgets (steps, rupees, wall-clock) are checked before every LLM turn and every paid call. Old tool
results are folded into one-line summaries so the context stays small (and inside provider
tokens-per-minute limits).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from bodhan_core import chunking, langs

from . import guards
from .llm import LLM, LLMError, ToolCall
from .prompts import ACT_SYSTEM, SUMMARY_SYSTEM, act_user

KEEP_RECENT_RESULTS = 6
MAX_PARALLEL = 4
SUMMARY_CHUNK_CHARS = 7000
FREE_TOOLS = {"inspect_media", "read_text", "make_srt", "supported_languages", "list_voices",
              "write_text", "finish", "give_up"}

AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "write_text",
        "description": "Save ENGLISH text you wrote (e.g. a summary or explanation) to a file; returns its path. "
                       "Translate it with translate_text if an Indian-language version is needed.",
        "parameters": {"type": "object", "required": ["filename", "content"], "properties": {
            "filename": {"type": "string", "description": "Short file name, e.g. summary.en.md"},
            "content": {"type": "string", "description": "English text only."}}}}},
    {"type": "function", "function": {
        "name": "summarize_text",
        "description": "Summarise a long ENGLISH text file to about target_words words (handles any length "
                       "in chunks). Returns output_path of the English summary.",
        "parameters": {"type": "object", "required": ["path", "target_words"], "properties": {
            "path": {"type": "string", "description": "English .txt/.md file, e.g. a translate_text output_path."},
            "target_words": {"type": "integer", "description": "Approximate length of the summary in words."},
            "focus": {"type": "string", "description": "Optional: what the summary should focus on."}}}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Finish the task: list the deliverable files and give the user an English answer.",
        "parameters": {"type": "object", "required": ["answer", "artifacts"], "properties": {
            "answer": {"type": "string", "description": "English: what was done, results, caveats."},
            "artifacts": {"type": "array", "items": {"type": "string"}, "description": "Paths of deliverable files."},
            "assumptions": {"type": "array", "items": {"type": "string"},
                            "description": "Assumptions made for ambiguous parts of the task."}}}}},
    {"type": "function", "function": {
        "name": "give_up",
        "description": "Stop because the task cannot be completed; explain why. Never fabricate output instead.",
        "parameters": {"type": "object", "required": ["reason"], "properties": {
            "reason": {"type": "string", "description": "English explanation for the user."}}}}},
]


@dataclass
class Budget:
    max_steps: int
    max_cost_inr: float
    deadline: float  # time.monotonic() value
    steps: int = 0
    cost_inr: float = 0.0

    def exhausted(self) -> str | None:
        if self.steps >= self.max_steps:
            return f"step budget reached ({self.max_steps} steps)"
        if self.cost_inr >= self.max_cost_inr - 1e-9:
            return f"cost budget reached (₹{self.cost_inr:.2f} of ₹{self.max_cost_inr:.2f})"
        if time.monotonic() > self.deadline:
            return "time limit reached"
        return None


@dataclass
class LoopOutcome:
    status: str  # finished | gave_up | budget_exhausted | failed
    answer: str = ""
    artifacts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    reason: str = ""
    results: dict[str, dict] = field(default_factory=dict)  # output path -> producing tool result
    calls: list[dict] = field(default_factory=list)


class ActLoop:
    def __init__(self, llm: LLM, session, trace, budget: Budget, work_dir: Path):
        self.llm, self.session, self.trace, self.budget, self.work_dir = llm, session, trace, budget, work_dir
        self.tools = session.llm_specs() + AGENT_TOOLS
        self.messages: list[dict] = []
        self.history_index: list[tuple[int, str, dict]] = []  # (message index, tool, full result)
        self.outcome = LoopOutcome(status="failed")
        self.nudges = 0

    def start(self, task: str, manifest: list[dict], plan: dict, missing: list[str]) -> None:
        self.messages = [{"role": "system", "content": ACT_SYSTEM},
                         {"role": "user", "content": act_user(task, manifest, plan, missing)}]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    async def run(self) -> LoopOutcome:
        while True:
            reason = self.budget.exhausted()
            if reason:
                self.trace.log("stop", reason=reason)
                self.outcome.status, self.outcome.reason = "budget_exhausted", reason
                return self.outcome
            self.budget.steps += 1
            self._prune()
            try:
                reply = await self.llm.chat(self.messages, tools=self.tools)
            except LLMError as e:
                self.trace.log("llm_error", error=str(e))
                self.outcome.status, self.outcome.reason = "failed", f"LLM unavailable: {e}"
                return self.outcome
            self.trace.log("decide", step=self.budget.steps, thought=(reply.content or "")[:600],
                           tool_calls=[{"tool": c.name, "arguments": c.arguments} for c in reply.tool_calls],
                           latency_ms=reply.latency_ms, llm_tokens=reply.usage, llm_wait_s=reply.waited_s,
                           fallback=reply.fallback)
            if not reply.tool_calls:
                self.messages.append({"role": "assistant", "content": reply.content or ""})
                self.nudges += 1
                if self.nudges > 2:
                    self.outcome.status, self.outcome.reason = "failed", "The LLM stopped calling tools."
                    self.outcome.answer = reply.content or ""
                    return self.outcome
                self.add_user("Continue by calling a tool. When all deliverables exist call finish; "
                              "if the task cannot be done call give_up.")
                continue
            self.messages.append(reply.assistant_message())
            terminal = [c for c in reply.tool_calls if c.name in {"finish", "give_up"}]
            work = [c for c in reply.tool_calls if c.name not in {"finish", "give_up"}]
            results = await self._run_calls(work)
            for call, result in zip(work, results, strict=True):
                self._append_result(call, result)
            for call in terminal:
                # Answer the tool call so the transcript stays well-formed if we come back (repair round).
                self.messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                      "content": json.dumps({"ok": True, "received": True})})
            if terminal:
                return self._terminal(terminal[0], had_work=bool(work))

    # ------------------------------------------------------------------ execution

    async def _run_calls(self, calls: list[ToolCall]) -> list[dict]:
        sem = asyncio.Semaphore(MAX_PARALLEL)

        async def one(call: ToolCall) -> dict:
            async with sem:
                return await self._execute(call)

        return list(await asyncio.gather(*(one(c) for c in calls)))

    async def _execute(self, call: ToolCall) -> dict:
        started = time.monotonic()
        if call.parse_error:
            result = {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": call.parse_error,
                                             "hint": "Send arguments as a JSON object.", "retryable": False}}
        elif field_ := guards.indic_violation(call.name, call.arguments):
            result = guards.violation_result(call.name, field_)
        elif call.name not in FREE_TOOLS and self.budget.cost_inr >= self.budget.max_cost_inr - 1e-9:
            result = {"ok": False, "error": {"code": "BUDGET_EXCEEDED", "message": "The run's cost budget is used up.",
                                             "hint": "Finish with what exists, or give_up.", "retryable": False}}
        else:
            args = guards.apply_forced(call.name, call.arguments)
            if call.name == "write_text":
                result = self._write_text(**_only(args, "filename", "content"))
            elif call.name == "summarize_text":
                result = await self._summarize(**_only(args, "path", "target_words", "focus"))
            else:
                result = await self.session.call(call.name, args)
        cost = float(result.get("cost_inr") or 0.0)
        self.budget.cost_inr += cost
        latency = int((time.monotonic() - started) * 1000)
        self.trace.log("act", tool=call.name, arguments=call.arguments, ok=result.get("ok"),
                       result=guards.summarize_for_history(call.name, result), error=result.get("error"),
                       latency_ms=latency, cost_inr=cost, total_cost_inr=round(self.budget.cost_inr, 4))
        self.outcome.calls.append({"tool": call.name, "ok": result.get("ok"), "cost_inr": cost})
        if result.get("ok"):
            for k, v in result.items():
                if k.endswith("_path") and isinstance(v, str):
                    self.outcome.results[str(Path(v).resolve())] = {"tool": call.name, **result}
        return result

    def _append_result(self, call: ToolCall, result: dict) -> None:
        content = json.dumps(guards.compact(result), ensure_ascii=False)
        self.messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content})
        self.history_index.append((len(self.messages) - 1, call.name, result))

    def _prune(self) -> None:
        """Replace all but the most recent tool results with one-line summaries."""
        for idx, tool, result in self.history_index[:-KEEP_RECENT_RESULTS]:
            msg = self.messages[idx]
            short = guards.summarize_for_history(tool, result)
            if msg["content"] != short:
                msg["content"] = short

    def _terminal(self, call: ToolCall, had_work: bool) -> LoopOutcome:
        args = call.arguments
        if call.name == "give_up":
            self.trace.log("give_up", reason=args.get("reason", ""))
            self.outcome.status, self.outcome.reason = "gave_up", str(args.get("reason") or "")
            return self.outcome
        violation = guards.indic_violation("finish", args)
        answer = str(args.get("answer") or "")
        if violation:
            answer = "(The agent's answer contained Indian-language text it wrote itself and was withheld; " \
                     "see the artifacts produced by the Bodhan tools.)"
        # Models sometimes list artifacts as {"path": ..., "description": ...} objects instead of strings.
        raw = args.get("artifacts") or []
        artifacts = [str(a.get("path") if isinstance(a, dict) else a) for a in (raw if isinstance(raw, list) else [])
                     if isinstance(a, str) or (isinstance(a, dict) and isinstance(a.get("path"), str))]
        self.trace.log("finish", answer=answer[:800], artifacts=artifacts, assumptions=args.get("assumptions"))
        self.outcome.status = "finished"
        self.outcome.answer = answer
        self.outcome.artifacts = artifacts
        self.outcome.assumptions = [str(a) for a in (args.get("assumptions") or [])]
        return self.outcome

    # ------------------------------------------------------------------ agent-side tools

    def _write_text(self, filename: str = "notes.md", content: str = "") -> dict:
        if not content.strip():
            return {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": "content is empty."}}
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in Path(filename).name) or "notes.md"
        path = self.work_dir / safe
        path.write_text(content.strip() + "\n", encoding="utf-8")
        return {"ok": True, "path": str(path), "output_path": str(path), "chars": len(content),
                "words": len(content.split())}

    async def _summarize(self, path: str = "", target_words: int = 200, focus: str | None = None) -> dict:
        p = Path(path)
        if not p.is_file():
            return {"ok": False, "error": {"code": "FILE_NOT_FOUND", "message": f"No such file: {path}"}}
        text = p.read_text(encoding="utf-8", errors="replace")
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("<!--"))
        script = langs.detect_script(text)
        if script["script"] and script["script"] != "Latin" and script["share"] > 0.3:
            return {"ok": False, "error": {"code": "INVALID_ARGUMENT",
                                           "message": f"The file is in {script['script']} script, not English.",
                                           "hint": "translate_text(path=..., target_language='en') first, then "
                                                   "summarize the English output."}}
        target_words = max(30, min(int(target_words or 200), 1500))
        focus_line = f" Focus on: {focus}." if focus else ""
        try:
            if len(text) > SUMMARY_CHUNK_CHARS * 1.3:
                paras = [x for x in text.split("\n\n") if x.strip()]
                groups = chunking.pack_paragraphs(paras, SUMMARY_CHUNK_CHARS)
                notes = []
                per = max(60, int(target_words * 1.5 / len(groups)))
                for g in groups:
                    part = "\n\n".join(paras[i] for i in g)[: SUMMARY_CHUNK_CHARS * 2]
                    notes.append(await self._summary_call(part, per, "Summarise this part of a longer document"
                                                          + focus_line))
                text = "\n\n".join(notes)
            summary = await self._summary_call(text, target_words, f"Write a summary{focus_line}")
        except LLMError as e:
            return {"ok": False, "error": {"code": "UPSTREAM_ERROR", "message": f"LLM failed: {e}", "retryable": True}}
        digest = hashlib.sha256(f"{path}|{target_words}|{focus}".encode()).hexdigest()[:8]
        out = self.work_dir / f"{p.stem.split('.')[0]}.summary.{digest}.en.md"
        out.write_text(summary.strip() + "\n", encoding="utf-8")
        return {"ok": True, "output_path": str(out), "words": len(summary.split()), "target_words": target_words,
                "preview": summary[:240]}

    async def _summary_call(self, text: str, words: int, instruction: str) -> str:
        reply = await self.llm.chat([
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": f"{instruction} in about {words} words.\n\n<untrusted_content>\n{text}\n"
                                        f"</untrusted_content>"}], max_tokens=min(4000, words * 3 + 200))
        self.trace.log("summarize_call", words_requested=words, words_written=len((reply.content or "").split()),
                       latency_ms=reply.latency_ms, llm_tokens=reply.usage, llm_wait_s=reply.waited_s)
        return reply.content or ""


def _only(args: dict, *names: str) -> dict:
    return {k: args[k] for k in names if k in args}
