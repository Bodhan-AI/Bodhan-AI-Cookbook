from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bodhan_agent.agent import BodhanTaskAgent


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bodhan_agent",
        description="Bodhan AI Autonomous Task Agent for multi-step Indian language workflows.",
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Plain-language task instruction to execute.",
    )
    parser.add_argument(
        "--files",
        required=True,
        help="Path to an input file or directory of input files.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory where result.md, artifacts/, trace.jsonl, and report.json are written.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum number of agent tool steps allowed before stopping cleanly (default: 30).",
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum budget in Indian Rupees (INR) before stopping cleanly (default: 20.0).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing output folder and reuse cached steps.",
    )
    args = parser.parse_args()

    agent = BodhanTaskAgent(
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
        out_dir=Path(args.out),
        resume=args.resume,
    )
    report = agent.run(task=args.task, files_path=args.files)

    summary_out = {
        "status": report["status"],
        "steps_taken": report["steps_taken"],
        "total_cost_inr": report["total_cost_inr"],
        "total_time_sec": report["total_time_sec"],
        "artifacts": report["artifacts"],
        "report_path": str((Path(args.out) / "report.json").resolve()),
        "result_md_path": str((Path(args.out) / "result.md").resolve()),
        "trace_path": str((Path(args.out) / "trace.jsonl").resolve()),
    }
    sys.stdout.write(json.dumps(summary_out, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
