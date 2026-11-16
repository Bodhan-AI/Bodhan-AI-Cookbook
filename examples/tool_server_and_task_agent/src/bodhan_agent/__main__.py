"""CLI entry point for the Bodhan task agent.

Usage:
    python -m bodhan_agent --task "..." --files path/to/inputs/ --out ./out
    python -m bodhan_agent --task "..." --files sample_data/ --out ./out \\
        --max-steps 30 --max-cost-inr 20 --mock
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow running from a source checkout without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bodhan_agent.agent import Agent, AgentConfig  # noqa: E402
from bodhan_mcp.dotenv import load_dotenv  # noqa: E402
from bodhan_mcp.errors import ToolError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bodhan_agent",
        description="Bodhan task agent: plan, act, verify, retry, stop.",
    )
    parser.add_argument("--task", required=True,
                        help="The task to complete, in plain language.")
    parser.add_argument("--files", required=True,
                        help="Input file, or a folder of input files.")
    parser.add_argument("--out", default="./out",
                        help="Output folder for result.md, artifacts/, "
                             "trace.jsonl and report.json. Default: ./out")
    parser.add_argument("--max-steps", type=int, default=30,
                        help="Max planner steps. Default: 30")
    parser.add_argument("--max-cost-inr", type=float, default=20.0,
                        help="Max spend in INR. Default: 20")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="Per-call timeout in seconds. Default: 60")
    parser.add_argument("--mock", action="store_true",
                        help="Use the mock Bodhan client and mock LLM "
                             "(local test run; no API calls, no cost).")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # .env in the recipe dir (keys never printed, never committed)
    args = build_parser().parse_args(argv)
    config = AgentConfig(
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
        timeout=args.timeout,
        out_dir=args.out,
        mock=args.mock,
    )
    try:
        agent = Agent(config)
        report = agent.run(args.task, args.files)
    except ToolError as exc:
        print(json.dumps(exc.to_dict(), indent=2), file=sys.stderr)
        return 1
    print(
        f"status={report['status']}  "
        f"steps={report['steps']}  "
        f"tool_calls={report['tool_calls']}  "
        f"cost=Rs {report['total_cost_inr']:.4f}  "
        f"duration={report['duration_seconds']}s"
    )
    if report["error"]:
        print(f"error: {report['error'].get('message', '')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
