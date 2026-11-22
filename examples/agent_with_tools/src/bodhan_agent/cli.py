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
from bodhan_agent.inputs import build_inventory, task_message  # noqa: E402
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
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    settings = load_llm_settings()
    out_dir = Path(args.out).absolute()
    rec = RunRecorder(out_dir)
    message = task_message(args.task, build_inventory(args.files), rec.artifacts_dir, args.max_steps, args.max_cost_inr)
    llm = ChatOpenAI(base_url=settings.base_url, api_key=settings.api_key, model=settings.model, temperature=0)

    # Langfuse tracing is optional: enabled only when both keys are present.
    lf, handler = None, None
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        lf, handler = get_client(), CallbackHandler()

    initial = initial_state(message)
    config: dict = {"run_name": "task-agent", "recursion_limit": 4 * args.max_steps + 10}
    trace_url = None

    # One MCP server process for the whole run; the tools are only valid inside this block.
    async with mcp_tools(output_root=rec.artifacts_dir) as tools:
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
               verifications=final["verifications"], stop_reason=final["stop_reason"], llm_turns=final["llm_turns"], call_timeout_s=args.call_timeout_s)
    print(answer)
    print(f"\n[status={final['status']} steps={rec.steps} cost=Rs {rec.cost_inr:.4f}]" + (f"\n[langfuse] {trace_url}" if trace_url else ""))
    if lf is not None:
        lf.shutdown()  # short-lived process: flush queued events before exit
    return 0 if final["status"] == "completed" else 1


def main() -> None:
    # Indic text and typographic characters crash print() on Windows consoles (cp1252).
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(run(_args())))
