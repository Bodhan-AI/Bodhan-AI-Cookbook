"""CLI entry point for Bodhan Task Agent."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Configure logging to stderr so stdout remains clean
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("bodhan_agent")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bodhan Task Agent: Autonomous multilingual Indic language task execution."
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Plain-language task description",
    )
    parser.add_argument(
        "--files",
        required=False,
        default=None,
        help="Path to input files directory or single input file",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Direct input text string to process",
    )
    parser.add_argument(
        "--out",
        default="./out",
        help="Output directory (default: ./out)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum number of steps before stopping (default: 30)",
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Maximum cost in INR before stopping (default: 20.0)",
    )

    args = parser.parse_args()

    if not args.files and not args.text:
        parser.error("At least one of --files or --text must be provided.")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "artifacts").mkdir(exist_ok=True)

    from bodhan_agent.loop import run

    state = asyncio.run(
        run(
            task=args.task,
            files_dir=args.files,
            text=args.text,
            out_dir=str(out_dir),
            max_steps=args.max_steps,
            max_cost_inr=args.max_cost_inr,
        )
    )

    logger.info(
        "Agent finished task: status=%s, spent=₹%.4f, steps=%d",
        state.status,
        state.spent_inr,
        state.steps_used,
    )

    # Completed, partial, or honest impossible exits cleanly (0). True execution failure or budget crash exits 1.
    if state.status in ("completed", "partial", "impossible"):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
