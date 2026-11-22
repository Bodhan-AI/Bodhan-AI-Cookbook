"""Command line entry point:  python -m bodhan_agent --task "..." --files inputs/ --out ./out"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .agent import RunConfig, run_task
from .llm import LLM, LLMError

# Exit codes: 0 = completed, or honestly reported as impossible; 2 = partial or stopped by a budget; 1 = failed.
EXIT = {"completed": 0, "impossible": 0, "partial": 2, "stopped": 2, "failed": 1}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bodhan_agent", description="Complete a language task with Bodhan's Indic models.")
    parser.add_argument("--task", required=True, help="The task in plain language.")
    parser.add_argument("--files", type=Path, default=None, help="A file or folder of input files.")
    parser.add_argument("--out", type=Path, default=Path("./out"), help="Folder for result.md, artifacts/, trace.jsonl and report.json.")
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum number of agent steps (LLM turns).")
    parser.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum Bodhan model cost in rupees.")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a code page that cannot print ₹ or Indian scripts
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    args.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(args.out / "agent.log", mode="w", encoding="utf-8")],
    )
    try:
        llm = LLM.from_env()
    except LLMError as exc:
        print(f"Cannot start: {exc}. See .env.example.", file=sys.stderr)
        return 1

    report = run_task(RunConfig(task=args.task, files=args.files, out=args.out, max_steps=args.max_steps, max_cost_inr=args.max_cost_inr), llm)
    print(f"status: {report['status']}  steps: {report['steps_used']}  cost: ₹{report['cost']['spent_inr']:.4f}  result: {args.out / 'result.md'}")
    return EXIT.get(report["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
