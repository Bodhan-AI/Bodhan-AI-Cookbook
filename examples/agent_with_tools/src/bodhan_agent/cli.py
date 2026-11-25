"""python -m bodhan_agent --task "..." --files path --out ./out [--max-steps 30] [--max-cost-inr 20]"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())  # must run BEFORE importing langfuse so it sees the keys

from langchain_openai import ChatOpenAI  # noqa: E402

from bodhan_agent.config import load_llm_settings  # noqa: E402
from bodhan_agent.graph import build_graph, initial_state  # noqa: E402
from bodhan_agent.llm_compat import async_client as llm_async_client  # noqa: E402
from bodhan_agent.inputs import build_inventory, inventory_flags, task_message  # noqa: E402
from bodhan_agent.mcp_client import mcp_tools  # noqa: E402
from bodhan_agent.trace import RunRecorder  # noqa: E402


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="bodhan_agent")
    p.add_argument("--task", required=True)
    p.add_argument("--files", nargs="*", default=[], help="Input file(s) or folder(s).")
    p.add_argument("--out", default="./out")
    p.add_argument("--max-steps", type=int, default=30, help="Maximum planning (LLM) steps.")
    p.add_argument("--max-cost-inr", type=float, default=5.0, help="Maximum Bodhan tool cost in rupees.")
    p.add_argument("--call-timeout-s", type=float, default=180.0, help="Hard timeout for each tool call.")
    p.add_argument("--mcp-url", default=os.environ.get("BODHAN_MCP_URL"), help="Use an already running HTTP server, e.g. http://127.0.0.1:8000/mcp (token from BODHAN_MCP_TOKEN). Default: start the stdio server.")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    """Run the task; an unexpected failure (LLM provider down, MCP server crash) still ends with a trace, report.json and result.md."""
    settings = load_llm_settings()
    out_dir = Path(args.out).absolute()
    rec = RunRecorder(out_dir)
    inventory = build_inventory(args.files)
    input_flags = inventory_flags(inventory)  # instruction-like text in text input files: the graph halts before any LLM call
    for flag in input_flags:
        rec.step(kind="security", tool=flag["tool"], result_summary=f"instruction-like text in {flag['tool']}: {flag['snippets']}; run halted", latency_s=0.0, cost_inr=0.0)
    try:
        return await _run(args, settings, out_dir, rec, inventory, input_flags)
    except BaseException as exc:  # includes ExceptionGroup from the MCP task group
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        leaf = exc
        while getattr(leaf, "exceptions", None):  # ExceptionGroup (Python 3.11+)
            leaf = leaf.exceptions[0]
        reason = f"{type(leaf).__name__}: {str(leaf)[:300]}"
        answer = f"The run stopped on an unexpected error before the task was finished: {reason}\nNothing further was done; see trace.jsonl for the steps that ran. Outputs already written to artifacts/ were not verified."
        rec.step(kind="error", result_summary=reason, latency_s=0.0, cost_inr=0.0)
        rec.finish(status="error", task=args.task, answer=answer, trace_url=None, max_steps=args.max_steps, max_cost_inr=args.max_cost_inr,
                   stop_reason=reason, call_timeout_s=args.call_timeout_s, security_flags=input_flags)
        print(answer)
        print(f"\n[status=error steps={rec.steps} cost=Rs {rec.cost_inr:.4f}]")
        return 1


async def _run(args: argparse.Namespace, settings, out_dir: Path, rec: RunRecorder, inventory: list[dict], input_flags: list[dict]) -> int:
    os.environ["BODHAN_WORK_DIR"] = str(out_dir / "work")  # intermediate text/segment files; inherited by the MCP server child
    message = task_message(args.task, inventory, rec.artifacts_dir, args.max_steps, args.max_cost_inr)
    llm = ChatOpenAI(base_url=settings.base_url, api_key=settings.api_key, model=settings.model, temperature=0,
                     max_retries=int(os.environ.get("LLM_MAX_RETRIES", "6")), http_async_client=llm_async_client(120.0))  # provider 429/503: SDK retries with backoff

    # Langfuse tracing is optional: enabled only when both keys are present.
    lf, handler = None, None
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        lf, handler = get_client(), CallbackHandler()

    initial = initial_state(message, input_flags)
    config: dict = {"run_name": "task-agent", "recursion_limit": 4 * args.max_steps + 10}
    trace_url = None

    # One MCP server process for the whole run; the tools are only valid inside this block.
    async with mcp_tools(output_root=rec.artifacts_dir, url=args.mcp_url) as tools:
        graph = build_graph(llm, tools, rec, max_steps=args.max_steps, max_cost_inr=args.max_cost_inr, call_timeout_s=args.call_timeout_s)

        if lf is None:
            final = await graph.ainvoke(initial, config)
        else:
            from langfuse import propagate_attributes

            config["callbacks"] = [handler]
            # One trace per task run. Root input = the task, root output = the final answer.
            with lf.start_as_current_observation(name="run-task", as_type="agent", input={"task": args.task}) as root:
                with propagate_attributes(
                    trace_name="bodhan-task-run",
                    tags=["bodhan-agent"],
                    metadata={
                        "llm_model": settings.model,
                        "max_steps": str(args.max_steps),
                        "max_cost_inr": str(args.max_cost_inr),
                        "run_id": uuid.uuid4().hex,
                    },
                    environment=os.environ.get("LANGFUSE_TRACING_ENVIRONMENT", "development"),
                ):
                    final = await graph.ainvoke(initial, config)
                root.update(
                    output=final["messages"][-1].content,
                    metadata={"status": final["status"], "cost_inr": str(round(rec.cost_inr, 6)), "steps": str(rec.steps)},
                )
                trace_url = lf.get_trace_url()

    answer = final["messages"][-1].content
    rec.finish(status=final["status"], task=args.task, answer=answer, trace_url=trace_url, max_steps=args.max_steps, max_cost_inr=args.max_cost_inr,
               verifications=final["verifications"], stop_reason=final["stop_reason"], llm_turns=final["llm_turns"], call_timeout_s=args.call_timeout_s,
               assumptions=final["final"].get("assumptions"), security_flags=final["security_flags"])
    print(answer)
    print(f"\n[status={final['status']} steps={rec.steps} cost=Rs {rec.cost_inr:.4f}]" + (f"\n[langfuse] {trace_url}" if trace_url else ""))
    if lf is not None:
        lf.shutdown()  # short-lived process: flush queued events before exit
    return 0 if final["status"] in ("completed", "impossible", "blocked") else 1  # an honest "impossible" or an injection halt is the correct outcome, not a failure


def main() -> None:
    # Indic text and typographic characters crash print() on Windows consoles (cp1252).
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(run(_args())))
