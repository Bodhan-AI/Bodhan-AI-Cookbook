"""Plan -> act -> verify -> finish loop with budgets and a replayable trace.

The agent imports the MCP server's tool registry directly, so the LLM sees exactly the
schemas and descriptions any third-party MCP client sees.
"""

from __future__ import annotations

import asyncio
import concurrent.futures as cf
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from bodhan_mcp import config, server
from bodhan_mcp.errors import BUDGET, ToolError
from bodhan_mcp.tools import Toolkit

from .budget import estimate_cost
from .prompts import FINISH_DESCRIPTION, SYSTEM
from .trace import Trace

MAX_RESULT_CHARS = 3500
AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "record_plan",
        "description": "Record or update your plan and assumptions. Call before any other tool.",
        "parameters": {"type": "object", "properties": {
            "steps": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}}},
            "required": ["steps"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": FINISH_DESCRIPTION,
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["completed", "partial", "not_possible"]},
            "answer": {"type": "string"},
            "artifacts": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}}},
            "required": ["status", "answer"]}}},
]


@dataclass
class Budget:
    max_steps: int = 30
    max_cost_inr: float = 20.0
    tool_timeout_sec: float = 300.0
    llm_timeout_sec: float = 120.0
    max_llm_turns: int = 60


@dataclass
class Outcome:
    status: str
    answer: str
    artifacts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    stop_reason: str = "finished"


def mcp_tool_schemas() -> list[dict]:
    out = []
    for t in server.mcp._tool_manager.list_tools():
        params = dict(t.parameters)
        params.pop("title", None)
        out.append({"type": "function", "function": {"name": t.name, "description": t.description,
                                                     "parameters": params}})
    return out


def make_llm():
    from openai import OpenAI

    missing = [k for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing LLM settings: {', '.join(missing)} (see .env.example)")
    client = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"], max_retries=3)
    model = os.environ["LLM_MODEL"]

    def chat(messages, tools, timeout):
        resp = client.chat.completions.create(model=model, messages=messages, tools=tools, tool_choice="auto",
                                              temperature=0, timeout=timeout)
        msg = resp.choices[0].message
        return {"content": msg.content, "tool_calls": [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments} for tc in (msg.tool_calls or [])]}

    return chat


class Agent:
    def __init__(self, toolkit: Toolkit, trace: Trace, budget: Budget, chat=None):
        self.kit = toolkit
        server._kit = toolkit           # the MCP tool functions use this toolkit
        self.trace = trace
        self.budget = budget
        self.chat = chat or make_llm()
        self.tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
        self.schemas = mcp_tool_schemas() + AGENT_TOOLS
        self.steps = 0
        self.planned = False
        self.plan: list[str] = []
        self.assumptions: list[str] = []
        self.seen_calls: dict[str, dict] = {}
        self.tool_results: list[dict] = []   # (name, args, result) for post-hoc verification

    @property
    def spent(self) -> float:
        return self.kit.client.ledger.total_cost

    # ------------------------------------------------------------------ tool execution
    def _run_tool(self, name: str, raw_args: str) -> dict:
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            return {"ok": False, "error": {"code": "invalid_arguments", "message": f"Bad JSON arguments: {e}"}}

        if name == "record_plan":
            self.planned = True
            self.plan = [str(s) for s in args.get("steps", [])]
            self.assumptions = list(dict.fromkeys(self.assumptions + [str(a) for a in args.get("assumptions", [])]))
            self.trace.log("plan", tool=name, arguments=args)
            return {"ok": True, "recorded": True}
        if name not in self.tools:
            return {"ok": False, "error": {"code": "unknown_tool", "message": f"No tool named {name!r}."}}
        if not self.planned:
            return {"ok": False, "error": {"code": "plan_required", "message": "Call record_plan first."}}

        key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if key in self.seen_calls:
            self.trace.log("tool", tool=name, arguments=args, result_summary="duplicate call, reused result", cost_inr=0)
            return {**self.seen_calls[key], "note": "Duplicate call: returning the earlier result (no model call)."}

        if self.steps >= self.budget.max_steps:
            return ToolError(BUDGET, f"Step budget of {self.budget.max_steps} tool calls is used up.",
                             hint="Call finish now with what you have.").to_dict()
        est = estimate_cost(name, args)
        remaining = round(self.budget.max_cost_inr - self.spent, 4)
        if est > remaining:
            err = ToolError(BUDGET, f"This call would cost about Rs {est:.2f} but only Rs {remaining:.2f} of the "
                                    f"Rs {self.budget.max_cost_inr} budget is left.",
                            hint="Do less (shorter text, fewer pages) or finish with status partial/not_possible.",
                            estimated_cost_inr=est, remaining_inr=remaining)
            self.trace.log("budget_block", tool=name, arguments=args, result_summary=err.message)
            return err.to_dict()

        self.steps += 1
        mark = self.kit.client.ledger.snapshot()
        t0 = time.monotonic()
        pool = cf.ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(asyncio.run, self.tools[name].run(args))  # validates args against the schema
        try:
            result = fut.result(timeout=self.budget.tool_timeout_sec)
        except cf.TimeoutError:
            result = {"ok": False, "error": {"code": "timeout", "retryable": True,
                                             "message": f"{name} exceeded {self.budget.tool_timeout_sec:.0f}s."}}
        except Exception as e:  # McpToolError carries our structured JSON
            result = _error_from_exception(e)
        finally:
            pool.shutdown(wait=False)
        ms = int((time.monotonic() - t0) * 1000)
        calls = self.kit.client.ledger.since(mark)
        cost = round(sum(c.cost_inr for c in calls if not c.cached), 4)
        self.trace.log("tool", tool=name, arguments=args, latency_ms=ms, cost_inr=cost,
                       model_calls=[f"{c.model}{' (cached)' if c.cached else ''}" for c in calls],
                       result_summary=_summary(result), ok=result.get("ok", False))
        if result.get("ok"):
            self.seen_calls[key] = result
        self.tool_results.append({"tool": name, "args": args, "result": result})
        return result

    # ------------------------------------------------------------------ main loop
    def _produced_artifacts(self) -> list[str]:
        from .verify import PRODUCERS
        out: list[str] = []
        for tr in self.tool_results:
            r = tr["result"]
            if tr["tool"] in PRODUCERS and r.get("ok") and r.get("path") and r["path"] not in out:
                out.append(r["path"])
        return out

    def run(self, task: str, inputs_overview: dict) -> Outcome:
        sys_prompt = SYSTEM.format(
            max_steps=self.budget.max_steps, max_cost=self.budget.max_cost_inr,
            asr=", ".join(config.languages("indic-transcribe")), mt=", ".join(config.languages("indic-translate")),
            tts=", ".join(config.languages("indic-speak")),
            names=", ".join(f"{k}={v}" for k, v in config.rules()["language_names"].items()))
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"Task: {task}\n\nInput files (inspected for you):\n"
                                        f"{json.dumps(inputs_overview, ensure_ascii=False, indent=1)[:6000]}"},
        ]
        self.trace.log("task", task=task, inputs=inputs_overview)
        nudged = False
        for turn in range(self.budget.max_llm_turns):
            t0 = time.monotonic()
            reply, err = None, None
            for attempt in range(3):  # malformed tool calls (e.g. a hallucinated tool name) are retried
                try:
                    reply = self.chat(messages, self.schemas, self.budget.llm_timeout_sec)
                    break
                except Exception as e:  # noqa: BLE001
                    err = e
                    self.trace.log("llm_error", attempt=attempt, message=str(e)[:500])
                    if "tool_use_failed" not in str(e) and "tool call validation" not in str(e).lower():
                        break
                    time.sleep(1 + attempt)
            if reply is None:
                produced = self._produced_artifacts()
                if produced:  # keep verified work instead of discarding it; status stays honest (partial)
                    return Outcome("partial", f"The planner LLM stopped responding ({err}). The outputs listed below "
                                   "were produced and checked by the tools before that; the task may be incomplete.",
                                   produced, assumptions=self.assumptions, stop_reason="llm_error")
                return Outcome("failed", f"The planner LLM could not be reached: {err}", stop_reason="llm_error")
            self.trace.log("llm", turn=turn, latency_ms=int((time.monotonic() - t0) * 1000),
                           content=(reply["content"] or "")[:1500], tool_calls=[c["name"] for c in reply["tool_calls"]])
            if not reply["tool_calls"]:
                if nudged:
                    # Some planners (e.g. gpt-oss) answer in prose instead of calling `finish`. Treat that as an
                    # implicit finish: claim every artifact the tools produced and let code verification decide
                    # the status; an answer with no tool output is never reported as completed.
                    produced = self._produced_artifacts()
                    return Outcome("completed" if produced else "partial",
                                   reply["content"] or "The agent stopped without calling finish.",
                                   produced, assumptions=self.assumptions, stop_reason="implicit_finish")
                nudged = True
                messages.append({"role": "assistant", "content": reply["content"] or ""})
                messages.append({"role": "user", "content": "Do not answer in prose. Either make the next tool call, "
                                 "or call the `finish` tool now with status, answer, artifacts and assumptions."})
                continue
            messages.append({"role": "assistant", "content": reply["content"] or "", "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in reply["tool_calls"]]})

            finish = next((c for c in reply["tool_calls"] if c["name"] == "finish"), None)
            others = [c for c in reply["tool_calls"] if c["name"] != "finish"]
            # independent tool calls in one turn run in parallel
            with cf.ThreadPoolExecutor(max_workers=4) as ex:
                results = list(ex.map(lambda c: self._run_tool(c["name"], c["arguments"]), others))
            for c, r in zip(others, results, strict=True):
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": _clip(r)})
            if finish:
                try:
                    args = json.loads(finish["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                messages.append({"role": "tool", "tool_call_id": finish["id"], "content": '{"ok": true}'})
                outcome = Outcome(args.get("status", "partial"), args.get("answer", ""),
                                  [str(a) for a in args.get("artifacts", [])],
                                  list(dict.fromkeys(self.assumptions + [str(a) for a in args.get("assumptions", [])])))
                self.trace.log("finish", arguments=args)
                return outcome
            if self.steps >= self.budget.max_steps or self.spent >= self.budget.max_cost_inr:
                messages.append({"role": "user", "content": "Budget exhausted. Call finish now with what you have."})
        return Outcome("partial", "Stopped: the agent hit its turn limit before finishing.",
                       assumptions=self.assumptions, stop_reason="turn_limit")


def _error_from_exception(e: Exception) -> dict:
    msg = str(e)
    if "{" in msg:
        try:
            parsed = json.loads(msg[msg.index("{"):])
            if isinstance(parsed, dict) and "error" in parsed:
                return parsed
        except json.JSONDecodeError:
            pass
    code = "invalid_arguments" if "validation error" in msg.lower() or "argument" in msg.lower() else "tool_error"
    return {"ok": False, "error": {"code": code, "message": msg[:600]}}


def _summary(result: dict) -> str:
    if not result.get("ok"):
        return f"ERROR {result.get('error', {}).get('code')}: {result.get('error', {}).get('message', '')[:200]}"
    keep = {k: v for k, v in result.items() if k in ("path", "transcript_json", "language", "duration_sec", "pages",
                                                       "chunks", "cues", "sentences", "verification", "checks",
                                                       "warnings", "empty_pages", "cost_inr")}
    return json.dumps(keep, ensure_ascii=False)[:600]


def _clip(result: dict) -> str:
    s = json.dumps(result, ensure_ascii=False)
    return s if len(s) <= MAX_RESULT_CHARS else s[:MAX_RESULT_CHARS] + '… [truncated; read files by path]"}'


def artifacts_dir(out: Path) -> Path:
    p = out / "artifacts"
    p.mkdir(parents=True, exist_ok=True)
    return p
