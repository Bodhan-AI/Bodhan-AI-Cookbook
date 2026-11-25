"""Runs one golden case through the real agent graph and returns everything the scorers need.

mode "stub": real LLM + real MCP server, paid Bodhan tools replaced by evals.stubs (Rs 0).
mode "live": everything real (spends Bodhan credit, so the case budget is also a hard cap).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

from bodhan_agent.config import load_llm_settings
from bodhan_agent.graph import FINISH, build_graph, initial_state
from bodhan_agent.inputs import build_inventory, inventory_flags, task_message
from bodhan_agent.mcp_client import mcp_tools
from bodhan_agent.trace import RunRecorder
from evals import cassettes
from evals.fixtures import build_inputs
from evals.stubs import StubBackend, with_stubs

TEXT_SUFFIXES = {".txt", ".md", ".json", ".srt"}
ROOT_DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "outputs" / "credit_ledger.json"


@dataclass
class Call:
    name: str
    args: dict
    result: str  # the full tool result text the planner saw


@dataclass
class CaseRun:
    case_id: str
    mode: str
    status: str = "error"
    answer: str = ""
    llm_turns: int = 0
    cost_inr: float = 0.0
    elapsed_s: float = 0.0
    calls: list[Call] = field(default_factory=list)  # the planner's tool calls, in order (finish_task excluded)
    finish_args: dict = field(default_factory=dict)
    verifications: list[dict] = field(default_factory=list)
    security_flags: list[dict] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    corpus: str = ""  # every text the tools produced or read: what the LLM may legitimately quote
    work_dir: str = ""
    cassette_hits: int = 0
    cassette_misses: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def rate_limited(self) -> bool:
        e = (self.error or "").lower()
        return any(t in e for t in ("rate limit", "request too large", "tokens per minute", "tokens per day"))  # provider limits, not agent behaviour

    def summary(self) -> dict:
        """Small JSON-safe view, used as the experiment output in Langfuse."""
        return {"status": self.status, "answer": self.answer, "llm_turns": self.llm_turns, "cost_inr": self.cost_inr, "elapsed_s": self.elapsed_s,
                "tool_calls": [{"tool": c.name, "args": c.args} for c in self.calls], "artifacts": self.artifacts,
                "verification": [{k: v for k, v in x.items() if k != "fingerprint"} for x in self.verifications],
                "security_flags": self.security_flags, "error": self.error, "cassette_hits": self.cassette_hits, "cassette_misses": self.cassette_misses}


def make_llm(max_retries: int = 8) -> ChatOpenAI:
    s = load_llm_settings()
    # Groq's free tier rate-limits by tokens per minute: let the client wait and retry instead of failing the case.
    return ChatOpenAI(base_url=s.base_url, api_key=s.api_key, model=s.model, temperature=0, max_retries=max_retries)


def _planner_calls(messages: list) -> tuple[list[Call], dict]:
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    calls, finish = [], {}
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        for c in m.tool_calls:
            if c["name"] == FINISH:
                finish = c["args"]
            else:
                r = results.get(c["id"])
                calls.append(Call(c["name"], c["args"], r.content if r and isinstance(r.content, str) else json.dumps(r.content) if r else ""))
    return calls, finish


def _corpus(work_dir: Path) -> str:
    parts = []
    for p in work_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
            try:
                parts.append(p.read_text(encoding="utf-8-sig"))
            except OSError:
                pass
    return "\n".join(parts)


async def run_case(case: dict, defaults: dict, fixtures: dict, mode: str, work_dir: Path, llm: ChatOpenAI, callbacks: list | None = None) -> CaseRun:
    run = CaseRun(case_id=case["id"], mode=mode, work_dir=str(work_dir))
    max_steps = int(case.get("max_steps", defaults["max_steps"]))
    max_cost = float(case.get("max_cost_inr", defaults["max_cost_inr"]))
    files = build_inputs(case, fixtures, work_dir / "inputs")
    out_dir = work_dir / "out"
    rec = RunRecorder(out_dir)
    os.environ["BODHAN_WORK_DIR"] = str(out_dir / "work")  # the MCP server child inherits this
    inventory = build_inventory(files)
    input_flags = inventory_flags(inventory)  # same up-front screening as the CLI: a flagged input file halts the run
    message = task_message(case["task"], inventory, rec.artifacts_dir, max_steps, max_cost)
    config: dict = {"run_name": f"eval-{case['id']}", "recursion_limit": 4 * max_steps + 10}
    if callbacks:
        config["callbacks"] = callbacks
    t0 = time.perf_counter()
    try:
        async with mcp_tools(output_root=rec.artifacts_dir) as tools:
            if mode == "stub":
                backend = StubBackend(case.get("stubs", {}), work_dir, cassettes.load(case["id"]))
                tools = with_stubs(tools, backend)
            graph = build_graph(llm, tools, rec, max_steps=max_steps, max_cost_inr=max_cost, call_timeout_s=120.0)
            final = await graph.ainvoke(initial_state(message, input_flags), config)
    except Exception as exc:  # a crashed run is a failed case, not a crashed eval session
        while isinstance(exc, BaseExceptionGroup):  # the MCP client wraps failures in a TaskGroup: show the real one
            exc = exc.exceptions[0]
        run.error, run.elapsed_s = f"{type(exc).__name__}: {exc}", round(time.perf_counter() - t0, 2)
        run.corpus, run.artifacts, run.cost_inr = _corpus(work_dir), rec.artifacts(), round(rec.cost_inr, 6)  # keep spend that happened before the crash
        return run

    answer = final["messages"][-1].content
    answer = answer if isinstance(answer, str) else json.dumps(answer)
    rec.finish(status=final["status"], task=case["task"], answer=answer, trace_url=None, max_steps=max_steps, max_cost_inr=max_cost,
               verifications=final["verifications"], stop_reason=final["stop_reason"], llm_turns=final["llm_turns"], call_timeout_s=120.0,
               assumptions=final["final"].get("assumptions"), security_flags=final["security_flags"])
    run.calls, run.finish_args = _planner_calls(final["messages"])
    run.status, run.answer, run.llm_turns = final["status"], answer, final["llm_turns"]
    run.cost_inr, run.elapsed_s = round(rec.cost_inr, 6), round(time.perf_counter() - t0, 2)
    run.verifications, run.security_flags, run.artifacts = final["verifications"], final["security_flags"], rec.artifacts()
    run.corpus = _corpus(work_dir)
    if mode == "stub":
        run.cassette_hits, run.cassette_misses = backend.hits, backend.misses
    return run


def load_golden(path: Path | None = None) -> dict:
    return json.loads((path or Path(__file__).with_name("golden.json")).read_text(encoding="utf-8"))


def expectations(case: dict, mode: str) -> dict[str, Any]:
    """The case's checks; in live mode `expect_live` replaces single keys (live output is not predictable)."""
    expect = dict(case["expect"])
    if mode == "live":
        expect.update(case.get("expect_live", {}))
    return expect
