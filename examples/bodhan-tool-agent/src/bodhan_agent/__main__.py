"""CLI entry point for Bodhan Task Agent.

Usage:
    python -m bodhan_agent \
      --task "Make a two-minute Tamil audio summary of this chapter" \
      --files path/to/inputs/ \
      --out ./out/run1 \
      [--max-steps 30] [--max-cost-inr 20]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bodhan_agent.agent import TaskAgent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bodhan_agent",
        description="Autonomous task agent for Bodhan Indic AI models",
    )
    parser.add_argument(
        "--task",
        required=True,
        type=str,
        help="Natural language instruction for the task",
    )
    parser.add_argument(
        "--files",
        required=True,
        nargs="+",
        help="One or more input files or a directory containing input files",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=str,
        help="Destination directory for output artifacts, trace.jsonl, and report.json",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum number of agent steps before stopping (default: 30)",
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum spend budget in Indian Rupees (default: 20.0)",
    )
    return parser.parse_args(argv)


def expand_input_files(file_args: list[str]) -> list[Path]:
    """Expands directories or individual files into a clean list of existing file paths."""
    resolved_paths: list[Path] = []
    for arg in file_args:
        p = Path(arg).resolve()
        if p.is_dir():
            # Add all non-hidden files inside directory
            for item in sorted(p.iterdir()):
                if item.is_file() and not item.name.startswith("."):
                    resolved_paths.append(item)
        elif p.is_file():
            resolved_paths.append(p)
        else:
            # If path doesn't exist, still pass it so the agent can fail honestly
            resolved_paths.append(p)
    return resolved_paths


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        input_files = expand_input_files(args.files)

        settings = load_settings()
        if not settings.llm_base_url:
            print(
                "Missing required LLM configuration: LLM_BASE_URL is unset. "
                "Please configure LLM_BASE_URL, LLM_MODEL, and LLM_API_KEY.",
                file=sys.stderr,
            )
            return 1

        agent = TaskAgent(
            task=args.task,
            files=input_files,
            out_dir=args.out,
            settings=settings,
            max_steps=args.max_steps,
            max_cost_inr=args.max_cost_inr,
        )

        report = agent.run()

        print(f"\nTask Finished with Status: {report.status.upper()}")
        print(f"Steps: {report.steps_taken} | Total Spent: ₹{report.total_cost_inr:.4f} | Time: {report.total_time_s:.2f}s")
        print(f"Artifacts: {len(report.artifacts)} produced -> saved in {args.out}/artifacts/")
        print(f"Detailed trace: {args.out}/trace.jsonl | Report: {args.out}/report.json")
        if report.failures:
            print(f"Failure / Note: {report.failures[0]}")

        return 0 if report.status == "success" else 1

    except Exception as e:
        print(f"\nTask failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
