"""CLI entry point for the Bodhan task agent."""

import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from .planner import ChatPlanner
from .run import run_task


def _cost(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("Use a finite nonnegative rupee amount.") from None
    if not number.is_finite() or number < 0:
        raise argparse.ArgumentTypeError("Use a finite nonnegative rupee amount.")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Bodhan document or audio task.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--files", required=True, nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-cost-inr", type=_cost, default=Decimal(20))
    args = parser.parse_args()
    if args.max_steps < 1:
        parser.error("--max-steps must be positive.")
    if not args.task.strip():
        parser.error("--task must contain the desired outcome.")
    try:
        with httpx.Client(timeout=30.0) as client:
            report = run_task(args.task, args.files, args.out.resolve(), args.max_steps, args.max_cost_inr,
                              lambda: ChatPlanner(client))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Could not start the run: {exc}\n")
    print(f"{report['status']}: {report['explanation']}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
