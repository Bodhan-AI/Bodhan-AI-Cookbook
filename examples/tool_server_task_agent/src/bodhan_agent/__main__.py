"""`python -m bodhan_agent --task ... --files ... --out ...`"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from bodhan_mcp import config

from .agent import RunConfig, run_agent
from .llm import ConfigError, LLMSettings, OpenAICompatLLM


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bodhan_agent", description="Run a language task with Bodhan's models through the MCP tool server.")
    p.add_argument("--task", required=True, help="The task in plain language.")
    p.add_argument("--files", nargs="*", default=[], help="Input files and/or directories.")
    p.add_argument("--out", default="./out", help="Output folder (result.md, artifacts/, trace.jsonl, report.json).")
    p.add_argument("--max-steps", type=int, default=30, help="Maximum tool steps (default 30).")
    p.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum Bodhan spend in rupees (default 20).")
    p.add_argument("--call-timeout-s", type=float, default=240.0, help="Per tool-call timeout in seconds.")
    p.add_argument("--max-wall-s", type=float, default=900.0, help="Overall wall-clock limit in seconds.")
    p.add_argument("--cache-dir", default=None, help="On-disk cache for paid calls (default: ~/.cache/bodhan_agent or $BODHAN_CACHE_DIR).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    import logging

    for noisy in ("httpx", "openai", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    config.load_env()
    out = Path(args.out).resolve()
    try:
        settings = LLMSettings.from_env()
    except ConfigError as exc:
        # An impossible-to-run setup is a failure, but unsupported/missing-file tasks are still refused honestly without the LLM.
        from .agent import Agent, Outcome, Trace, _preflight_without_tools

        out.mkdir(parents=True, exist_ok=True)
        cfg = RunConfig(args.task, args.files, out)
        agent = Agent(cfg, llm=None)  # type: ignore[arg-type]
        agent.trace = Trace(out / "trace.jsonl")
        early = asyncio.run(_preflight_without_tools(agent))
        agent.outcome = early or Outcome("failed", f"The agent could not start: {exc}", [])
        agent.finalize_outputs()
        print(str(exc), file=sys.stderr)
        return 0 if early else 1
    import os

    cache = Path(args.cache_dir or os.environ.get("BODHAN_CACHE_DIR") or Path.home() / ".cache" / "bodhan_agent").resolve()
    cfg = RunConfig(task=args.task, files=args.files, out=out, max_steps=args.max_steps, max_cost_inr=args.max_cost_inr,
                    call_timeout_s=args.call_timeout_s, max_wall_s=args.max_wall_s, cache_dir=cache, llm_model=settings.model)
    report = asyncio.run(run_agent(cfg, OpenAICompatLLM(settings)))
    print(f"[{report['status']}] cost Rs {report['total_cost_inr']:.3f}, {report['total_time_s']:.1f}s, {report['steps']['tool_calls']} tool calls -> {out}")
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
