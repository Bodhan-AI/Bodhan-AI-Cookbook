
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .agent import DEFAULT_MAX_TIME_SECONDS, run_task


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bodhan-agent",
        description="Run tasks using Bodhan Indic AI tools and an LLM planner.",
    )
    parser.add_argument("--task", required=True, help="Natural-language task")
    parser.add_argument(
        "--files",
        nargs="*",
        default=[],
        help="Input file paths",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum number of tool calls",
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum Bodhan task cost in INR",
    )
    parser.add_argument(
        "--max-time-seconds",
        type=float,
        default=DEFAULT_MAX_TIME_SECONDS,
        help="Maximum overall task wall-clock time in seconds",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.max_steps < 1:
        parser.error("--max-steps must be at least 1")
    if not math.isfinite(args.max_time_seconds) or args.max_time_seconds <= 0:
        output_dir = Path(args.out)
        output_dir.mkdir(parents=True, exist_ok=True)
        message = "--max-time-seconds must be a finite number greater than zero"
        report = {
            "status": "failed",
            "failure_type": "invalid_time_budget",
            "configured_time_limit_seconds": str(args.max_time_seconds),
            "elapsed_seconds": 0.0,
            "remaining_time_seconds": 0.0,
            "tool_call_count": 0,
            "failure_reason": message,
        }
        (output_dir / "report.json").write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
        print(f"Agent failed: {message}", file=sys.stderr)
        raise SystemExit(1) from None
    if not math.isfinite(args.max_cost_inr) or args.max_cost_inr <= 0:
        output_dir = Path(args.out)
        output_dir.mkdir(parents=True, exist_ok=True)
        message = "--max-cost-inr must be a finite number greater than zero"
        report = {
            "status": "failed",
            "tool_call_count": 0,
            "configured_budget_inr": str(args.max_cost_inr),
            "bodhan_cost_estimate": {
                "transcribe": "0",
                "translate": "0",
                "speak": "0",
                "ocr": "0",
            },
            "bodhan_total_estimate_inr": "0",
            "reserved_bodhan_cost_inr": "0",
            "reconciled_bodhan_cost_inr": "0",
            "reconciled_bodhan_cost_by_service": {
                "transcribe": "0",
                "translate": "0",
                "speak": "0",
                "ocr": "0",
            },
            "estimated_bodhan_cost_inr": "0",
            "estimated_bodhan_cost_by_service": {
                "transcribe": "0",
                "translate": "0",
                "speak": "0",
                "ocr": "0",
            },
            "unknown_bodhan_usage_or_cost": [],
            "unknown_attempts_by_service": {
                "transcribe": 0,
                "translate": 0,
                "speak": 0,
                "ocr": 0,
            },
            "calls": {
                "transcribe": 0,
                "translate": 0,
                "speak": 0,
                "ocr": 0,
            },
            "llm_usage": {},
            "elapsed_seconds": 0.0,
            "failure_reason": message,
        }
        (output_dir / "report.json").write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
        print(f"Agent failed: {message}", file=sys.stderr)
        raise SystemExit(1) from None

    try:
        result = run_task(
            args.task,
            [Path(item) for item in args.files],
            Path(args.out),
            max_steps=args.max_steps,
            max_cost_inr=args.max_cost_inr,
            max_time_seconds=args.max_time_seconds,
        )

        output_dir = Path(args.out)
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "result.md"
        result_path.write_text(result + "\n", encoding="utf-8")

        print(result)
        print(f"\nResult saved to: {result_path.resolve()}")

    except Exception as exc:
        print(f"Agent failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
