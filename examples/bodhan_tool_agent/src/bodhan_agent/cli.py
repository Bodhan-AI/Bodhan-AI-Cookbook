"""Command-line interface (CLI) for Bodhan Autonomous Task Agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from bodhan_agent.orchestrator import AgentOrchestrator
from bodhan_mcp.errors import BodhanError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bodhan_agent",
        description="Bodhan Autonomous Task Agent for multilingual AI workflows",
    )
    parser.add_argument(
        "--task",
        type=str,
        required=False,
        help="Natural language task description (e.g. 'Make a two-minute Tamil audio summary of this chapter')",
    )
    parser.add_argument(
        "--files",
        type=str,
        default=None,
        help="Path to input file or directory containing files to process",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="./out",
        help="Destination directory for output artifacts, traces, and reports (default: ./out)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum allowed execution steps (default: 30)",
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum budget in INR (default: 20.0)",
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help="Resume an existing task by Task ID from checkpoint",
    )

    args = parser.parse_args(argv)

    if not args.task and not args.resume:
        parser.error("Either --task or --resume is required.")

    out_dir = Path(args.out).resolve()
    orchestrator = AgentOrchestrator(
        out_dir=out_dir,
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
    )

    try:
        res = orchestrator.run(
            task_prompt=args.task or "Resumed task",
            files_path=args.files,
            resume_task_id=args.resume,
        )
        print(f"Task status: {res['status'].upper()}")
        print(f"Runtime: {res['runtime_s']:.2f}s | Cost: ₹{res['cost_inr']:.4f}")
        print(f"Outputs saved to: {out_dir}")
        return 0 if res["status"] in {"completed", "partial"} else 1

    except BodhanError as e:
        print(f"Agent error ({e.code}): {e.message}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
