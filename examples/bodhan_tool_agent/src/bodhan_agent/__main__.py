"""CLI Entrypoint for python -m bodhan_agent.

Usage:
    python -m bodhan_agent \
      --task "Make a two-minute Tamil audio summary of this chapter" \
      --files path/to/inputs/ \
      --out ./out \
      [--max-steps 30] [--max-cost-inr 20]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.bodhan_agent.agent import run_task_agent
from src.common.config import ensure_utf8_streams
from src.common.logger import get_logger

# Ensure UTF-8 console output
ensure_utf8_streams()

logger = get_logger("agent.cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bodhan Autonomous Task Agent CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--task",
        type=str,
        required=True,
        help="Plain-language description of the task to perform.",
    )
    parser.add_argument(
        "--files",
        type=str,
        default=None,
        help="Path to input files or directory on disk.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="./out",
        help="Directory where result.md, artifacts/, trace.jsonl, and report.json are written.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum number of reasoning/tool execution steps.",
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum estimated Bodhan usage budget in INR; excludes LLM/gateway charges.",
    )

    args = parser.parse_args(argv)

    logger.info("Starting Bodhan Task Agent...")
    logger.info("Task: %s", args.task)
    logger.info("Inputs: %s", args.files or "None")
    logger.info("Output directory: %s", args.out)
    logger.info(
        "Estimated Bodhan budget: ₹%.2f (LLM/gateway charges excluded) | Max Steps: %d",
        args.max_cost_inr,
        args.max_steps,
    )

    result = asyncio.run(
        run_task_agent(
            task=args.task,
            files_dir=args.files,
            out_dir=args.out,
            max_steps=args.max_steps,
            max_cost_inr=args.max_cost_inr,
        )
    )

    logger.info("Execution Finished: Status = %s", result["status"].upper())
    logger.info(
        "Total Steps: %d | Estimated Bodhan cost: ₹%.2f",
        result["total_steps"],
        result["estimated_bodhan_cost_inr"],
    )
    logger.info("Run outputs saved to: %s", result["output_dir"])
    if result.get("archive_dir"):
        logger.info("Run archive snapshot: %s", result["archive_dir"])
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
