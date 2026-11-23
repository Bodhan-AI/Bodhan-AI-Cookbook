"""Run the task agent.

    python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" \\
        --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]

Writes out/result.md, out/artifacts/, out/trace.jsonl and out/report.json.

Exit status: 0 when the run ended cleanly and wrote its report, whatever the task status
(completed, partial, infeasible, budget_exhausted, ... — see report.json "status");
1 for a configuration or usage error; 2 if the agent itself crashed (a report is still written).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bodhan_agent", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task", help="the task in plain language")
    p.add_argument("--files", nargs="*", default=[], help="input files and/or directories")
    p.add_argument("--out", default="./out", help="output folder for this run (default ./out)")
    p.add_argument("--max-steps", type=int, default=30, help="maximum agent steps (LLM turns), default 30")
    p.add_argument("--max-cost-inr", type=float, default=20.0, help="maximum Bodhan spend in rupees, default 20")
    p.add_argument("--call-timeout-s", type=float, default=float(os.environ.get("BODHAN_AGENT_CALL_TIMEOUT_S", 300)),
                   help="timeout for any single tool call, default 300")
    p.add_argument("--max-time-s", type=float, default=float(os.environ.get("BODHAN_AGENT_MAX_TIME_S", 900)),
                   help="wall-clock limit for the whole run, default 900")
    p.add_argument("--server", choices=["inproc", "stdio"], default=os.environ.get("BODHAN_AGENT_SERVER", "inproc"),
                   help="reach bodhan_mcp in-process (default) or by launching `python -m bodhan_mcp` over stdio")
    p.add_argument("--replay", metavar="TRACE", help="re-run a recorded trace.jsonl using its recorded LLM decisions")
    p.add_argument("--log-level", default=os.environ.get("BODHAN_LOG_LEVEL", "WARNING"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv(find_dotenv(usecwd=True))  # .env from the current directory, not the package location
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from .agent import Agent
    from .budget import Budget
    from .llm import LLMError, LLMResponse, OpenAICompatibleLLM, ScriptedLLM
    from .trace import load, replay_responses

    task, files = args.task, list(args.files)
    if args.replay:
        events = load(Path(args.replay))
        start = next((e for e in events if e["type"] == "run_start"), None)
        if start is None:
            print("Not a bodhan_agent trace (no run_start event).", file=sys.stderr)
            return 1
        task, files = task or start["task"], files or start["files"]
        llm = ScriptedLLM([LLMResponse.from_dict(r) for r in replay_responses(events, Path(args.out))],
                          model=f"replay:{start.get('llm_model')}")
    else:
        if not task:
            print("--task is required (or --replay TRACE).", file=sys.stderr)
            return 1
        try:
            llm = OpenAICompatibleLLM.from_env()
        except LLMError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if args.max_steps < 1 or args.max_cost_inr < 0:
        print("--max-steps must be >= 1 and --max-cost-inr >= 0.", file=sys.stderr)
        return 1

    budget = Budget(max_steps=args.max_steps, max_cost_inr=args.max_cost_inr, max_time_s=args.max_time_s,
                    call_timeout_s=args.call_timeout_s)
    state = asyncio.run(Agent(llm, budget=budget, server_mode=args.server).run(task, files, Path(args.out)))
    report = json.loads((Path(args.out) / "report.json").read_text(encoding="utf-8"))
    print(json.dumps({"status": report["status"], "cost_inr": report["cost_inr"]["total"], "time_s": report["time_s"],
                      "artifacts": [a["file"] for a in report["artifacts"]],
                      "result": str(Path(args.out) / "result.md")}, ensure_ascii=False))
    return 2 if any(e.get("code") == "agent_crash" for e in state.errors) else 0


if __name__ == "__main__":
    sys.exit(main())
