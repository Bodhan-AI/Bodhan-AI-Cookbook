from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bodhan_agent", description="Run a plain-language task with Bodhan tools.")
    ap.add_argument("--task", required=True, help="What to do, in plain language")
    ap.add_argument("--files", nargs="*", default=[], help="Input files and/or folders")
    ap.add_argument("--out", default="./out", help="Output folder (result.md, artifacts/, trace.jsonl, report.json)")
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--max-cost-inr", type=float, default=20.0)
    ap.add_argument("--max-seconds", type=float, default=600)
    ap.add_argument("--call-timeout", type=float, default=180, help="Per tool call timeout in seconds")
    ap.add_argument("--use-mcp", action="store_true", help="Reach the tools through the stdio MCP server")
    a = ap.parse_args(argv)

    from bodhan_mcp.config import load_env
    load_env()
    from .agent import Agent
    from .llm import LLM, LLMError
    out = Path(a.out)
    try:
        llm = LLM()
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    files = [f for item in a.files for f in item.split(",") if f]
    agent = Agent(a.task, files, out, llm, max_steps=a.max_steps, max_cost_inr=a.max_cost_inr,
                  max_seconds=a.max_seconds, call_timeout_s=a.call_timeout, use_mcp=a.use_mcp)
    async def go():
        try:
            return await agent.run()
        finally:
            await llm.aclose()

    report = asyncio.run(go())
    if report.get("error"):
        print(f"error: {report['error']}", file=sys.stderr)
    print(f"status: {report['status']} | cost: ₹{report['total_cost_inr']:.2f} | steps: {report['steps']} | "
          f"time: {report['elapsed_s']}s\nresult: {out / 'result.md'}")
    return 0 if report["status"] in ("completed", "impossible") else 1


if __name__ == "__main__":
    sys.exit(main())
