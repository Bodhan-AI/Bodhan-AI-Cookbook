"""CLI Entrypoint for Bodhan Task Agent.

Usage:
    python -m bodhan_agent --task "..." --files file1 file2 --out ./runs/run-01
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from bodhan_agent.models import ExecutionStatus
from bodhan_agent.runner import AgentRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bodhan-agent",
        description="Autonomous Task Agent for Bodhan Indic AI models with budget and verification enforcement.",
    )
    parser.add_argument(
        "--task",
        type=str,
        required=True,
        help="High-level Indic language processing task description.",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=[],
        help="Input file references (audio, images, PDFs, text) to process.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="./out",
        help="Directory to save run reports, trace log, and generated artifacts.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum execution step budget (default: 30).",
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum allowed monetary budget in INR (default: ₹20.00).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Overall task execution timeout in seconds (default: 300s).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed debug logging.",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
        datefmt="%H:%M:%S",
    )

    runner = AgentRunner(
        task=args.task,
        files=args.files,
        output_dir=args.out,
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
        timeout_seconds=args.timeout,
    )

    report = await runner.run()

    print("\n" + "=" * 60)
    print(f"Task Run Status: {report.status.value}")
    print(f"Steps Taken:     {report.steps_taken} / {report.max_steps}")
    print(f"Total Cost:      ₹{report.total_cost_inr:.4f} (Max: ₹{report.max_cost_inr:.2f})")
    print(f"Runtime:         {report.duration_seconds:.2f}s")
    print(f"Output Dossier:  {Path(args.out).resolve()}")
    if report.reason:
        print(f"Notice:          {report.reason}")
    print("=" * 60 + "\n")

    if report.status in (ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL):
        return 0
    elif report.status == ExecutionStatus.IMPOSSIBLE:
        # Clean honest stop
        return 0
    else:
        return 1


def main() -> None:
    try:
        sys.exit(asyncio.run(main_async()))
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted by user.\n")
        sys.exit(130)


if __name__ == "__main__":
    main()
