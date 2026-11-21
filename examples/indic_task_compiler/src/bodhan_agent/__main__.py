"""CLI entry point for `python -m bodhan_agent`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from bodhan_agent.config import RunConfig, load_env_quietly
from bodhan_agent.executor import run_agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bodhan_agent",
        description="Run the plan-checked, CaMeL-isolated Bodhan Indic Task Agent.",
    )
    parser.add_argument("--task", required=True, help="Plain-language task description.")
    parser.add_argument(
        "--files",
        nargs="+",
        action="extend",
        default=[],
        help="Input file(s) or directory path(s).",
    )
    parser.add_argument("--out", required=True, help="Output folder for result.md, artifacts/, trace.jsonl, report.json.")
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum LLM + tool steps (default: 30).")
    parser.add_argument("--max-cost-inr", type=float, default=20.0, help="Hard spend cap in INR (default: 20.0).")
    parser.add_argument("--timeout-s", type=float, default=150.0, help="Per-call timeout in seconds (default: 150).")
    parser.add_argument("--cache-dir", default=None, help="Optional Bodhan cache directory override.")
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="Call bodhan_mcp tools in-process instead of spawning a stdio subprocess.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env_quietly()
    parser = build_parser()
    args = parser.parse_args(argv)

    input_paths = [Path(p) for p in args.files]
    cfg = RunConfig(
        task=args.task,
        input_paths=input_paths,
        out_dir=Path(args.out),
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
        timeout_s=args.timeout_s,
        use_stdio_mcp=not args.in_process,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
    )

    report = asyncio.run(run_agent(cfg))
    summary = {
        "status": report.status,
        "total_cost_inr": report.total_cost_inr,
        "steps_used": report.steps_used,
        "total_time_s": report.total_time_s,
        "out_dir": str(cfg.out_dir.resolve()),
        "reason": report.reason,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.status in ("success", "infeasible") else 1


if __name__ == "__main__":
    sys.exit(main())
