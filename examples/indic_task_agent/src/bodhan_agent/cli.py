"""Command line: python -m bodhan_agent --task "..." --files path/ --out ./out [--max-steps 30] [--max-cost-inr 20]"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .agent import Agent, Budgets
from .llm import LLMConfigError, OpenAICompatibleLLM
from .mcp_bridge import ToolBridge
from .report import write_outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m bodhan_agent", description="Run an Indian-language task with Bodhan tools."
    )
    p.add_argument("--task", required=True, help="The task in plain language.")
    p.add_argument("--files", nargs="*", default=[], help="Input file(s) or a directory of inputs.")
    p.add_argument("--out", default="./out", help="Output folder (result.md, artifacts/, trace.jsonl, report.json).")
    p.add_argument("--max-steps", type=int, default=30, help="Maximum tool calls.")
    p.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum Bodhan spend in rupees.")
    p.add_argument("--tool-timeout", type=float, default=300.0, help="Per tool-call timeout in seconds.")
    p.add_argument("--max-wall-seconds", type=float, default=900.0, help="Overall time limit in seconds.")
    p.add_argument("--llm-timeout", type=float, default=120.0, help="Per LLM request timeout in seconds.")
    p.add_argument(
        "--max-prompt-tokens",
        type=int,
        default=int(os.environ.get("LLM_MAX_PROMPT_TOKENS") or 16000),
        help="Shorten older context above this many prompt tokens (env LLM_MAX_PROMPT_TOKENS; ~5500 for Groq's free tier).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    out = Path(args.out)
    budgets = Budgets(
        max_steps=max(1, args.max_steps),
        max_cost_inr=max(0.0, args.max_cost_inr),
        tool_timeout_s=args.tool_timeout,
        max_wall_s=args.max_wall_seconds,
        max_prompt_tokens=args.max_prompt_tokens,
    )
    try:
        llm = OpenAICompatibleLLM.from_env(timeout_s=args.llm_timeout)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        out.mkdir(parents=True, exist_ok=True)
        (out / "artifacts").mkdir(exist_ok=True)
        (out / "trace.jsonl").write_text(
            json.dumps({"seq": 0, "kind": "error", "message": str(exc)}) + "\n", encoding="utf-8"
        )
        (out / "report.json").write_text(
            json.dumps({"status": "failed", "stop_reason": str(exc), "cost": {"total_inr": 0}}, indent=2),
            encoding="utf-8",
        )
        (out / "result.md").write_text(
            f"# Task result\n\n**Status:** ❌ failed\n\nThe agent could not start: {exc}\n", encoding="utf-8"
        )
        return 2

    agent = Agent(args.task, args.files, out, budgets, llm, lambda env: ToolBridge(env=env))
    state = asyncio.run(agent.run())
    report = write_outputs(state, llm.model)
    print(
        json.dumps(
            {
                "status": report["status"],
                "stop_reason": report["stop_reason"],
                "cost_inr": report["cost"]["total_inr"],
                "steps": report["steps"]["tool_calls"],
                "elapsed_s": report["time"]["elapsed_s"],
                "artifacts": [a["path"] for a in report["artifacts"]],
                "result": str(out / "result.md"),
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0 if report["status"] in {"success", "partial", "impossible"} else 1


if __name__ == "__main__":
    sys.exit(main())
