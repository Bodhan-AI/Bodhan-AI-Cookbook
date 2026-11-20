"""Command line: ``python -m bodhan_agent --task "..." --files path/ --out ./out``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from bodhan_mcp.bodhan import BodhanClient, Budget
from dotenv import find_dotenv, load_dotenv

from .agent import Agent, RunConfig
from .llm import LLMClient, LLMError

# 0: done (completed, or honestly reported as impossible). 2: stopped early with partial work. 1: failed.
EXIT_CODES = {"completed": 0, "impossible": 0, "partial": 2, "budget_exhausted": 2, "step_limit_reached": 2, "failed": 1}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bodhan_agent", description="Run one Indian-language task with Bodhan tools.")
    parser.add_argument("--task", required=True, help="the task in plain language")
    parser.add_argument("--files", nargs="*", default=[], help="input files or folders")
    parser.add_argument("--out", default="./out", help="output folder (result.md, artifacts/, trace.jsonl, report.json)")
    parser.add_argument("--max-steps", type=int, default=30, help="maximum LLM turns")
    parser.add_argument("--max-cost-inr", type=float, default=20.0, help="maximum spend on Bodhan models, in rupees")
    parser.add_argument("--call-timeout", type=float, default=90.0, help="seconds per Bodhan call")
    parser.add_argument("--llm-timeout", type=float, default=120.0, help="seconds per LLM call")
    parser.add_argument("--cache-dir", default=None, help="model-output cache (default ~/.cache/bodhan_mcp/cache)")
    parser.add_argument("--keep-work", action="store_true", help="keep intermediate audio and page images in out/.work")
    parser.add_argument("--quiet", action="store_true", help="do not print a line per step (the trace is written either way)")
    args = parser.parse_args(argv)

    load_dotenv(find_dotenv(usecwd=True))
    for noisy in ("httpx", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    env = dict(os.environ)
    out = Path(args.out).expanduser()
    cache = Path(args.cache_dir or env.get("BODHAN_CACHE_DIR") or Path.home() / ".cache" / "bodhan_mcp" / "cache").expanduser()
    client = BodhanClient(cache_dir=cache, budget=Budget(args.max_cost_inr), env=env, timeout_s=args.call_timeout)
    llm, llm_error = None, None
    try:
        llm = LLMClient.from_env(env, timeout_s=args.llm_timeout)
    except LLMError as exc:
        llm_error = exc.message

    config = RunConfig(task=args.task, inputs=[Path(f) for f in args.files], out_dir=out, max_steps=args.max_steps,
                       max_cost_inr=args.max_cost_inr, keep_work=args.keep_work,
                       progress=None if args.quiet else (lambda line: print(line, file=sys.stderr, flush=True)))
    report = Agent(config, llm=llm, client=client, llm_error=llm_error).run()
    print(f"status: {report['status']}  steps: {report['steps_used']}  cost: INR {report['cost_inr']:.4f}  time: {report['duration_s']} s")
    print(f"result: {out / 'result.md'}")
    return EXIT_CODES.get(report["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
