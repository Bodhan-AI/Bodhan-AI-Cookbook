"""
Command-line interface for the Bodhan Task Agent.
"""

import sys
import argparse
from .agent import TaskAgent


def parse_args():
    parser = argparse.ArgumentParser(
        description="Bodhan Autonomous Task Agent: Completes multi-step Indic language tasks using Bodhan models."
    )
    parser.add_argument(
        "--task",
        type=str,
        required=True,
        help="Plain-language description of the task to perform."
    )
    parser.add_argument(
        "--files",
        type=str,
        default=None,
        help="Path to input file or directory of input files."
    )
    parser.add_argument(
        "--out",
        type=str,
        default="./out",
        help="Output directory for results, trace, artifacts, and report."
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum allowed execution steps before graceful termination (default: 30)."
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum allowed cost budget in INR before graceful termination (default: 20.0)."
    )
    return parser.parse_args()


def main():
    args = parse_args()
    agent = TaskAgent(
        task=args.task,
        files_path=args.files,
        out_dir=args.out,
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
    )
    report = agent.run()

    print("\n" + "=" * 60)
    print(f"Task Agent Execution Status: {report.status}")
    print(f"Total Steps: {report.total_steps}")
    print(f"Total Cost: ₹{report.total_cost_inr:.4f} INR")
    print(f"Total Time: {report.total_duration_seconds:.2f}s")
    print(f"Output Directory: {agent.out_dir}")
    print("=" * 60 + "\n")

    if report.status == "FAILED":
        sys.exit(1)


if __name__ == "__main__":
    main()
