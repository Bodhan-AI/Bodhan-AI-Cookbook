from __future__ import annotations

import argparse

from dotenv import load_dotenv

from .runner import run_task


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run an unattended Bodhan language task.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--files", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-cost-inr", type=float, default=20)
    args = parser.parse_args()

    report = run_task(
        task=args.task,
        files=args.files,
        out=args.out,
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
    )
    print(report["status"])


if __name__ == "__main__":
    main()
