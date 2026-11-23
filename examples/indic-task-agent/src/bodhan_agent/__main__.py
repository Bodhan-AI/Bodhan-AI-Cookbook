"""
Command-Line Interface for the Bodhan Autonomous Task Agent.
Usage:
    python -m bodhan_agent \
        --task "Make a two-minute Tamil audio summary of this chapter" \
        --files path/to/inputs/ \
        --out ./out \
        [--max-steps 30] [--max-cost-inr 20]
"""

import argparse
import logging
import sys
from pathlib import Path

from bodhan_agent.agent import TaskAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("bodhan.agent.cli")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Bodhan Autonomous Multimodal Task Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m bodhan_agent --task "Translate this video into French" --files ./inputs/video.mp4 --out ./out
  python -m bodhan_agent --task "Make a two-minute Tamil audio summary of this chapter" --files ./inputs/chapter.pdf --out ./out
  python -m bodhan_agent --task "Transcribe this Hindi voice note and give me English subtitles" --files ./inputs/note.wav --out ./out
        """
    )

    parser.add_argument(
        "--task",
        type=str,
        required=True,
        help="Plain-language description of the task to complete."
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="Path to input file(s) or directory of input files."
    )
    parser.add_argument(
        "--out",
        type=str,
        default="./out",
        help="Output directory to write result.md, artifacts/, trace.jsonl, and report.json (default: ./out)."
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum planning and execution steps before halting (default: 30)."
    )
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=20.0,
        help="Budget ceiling in Indian Rupees (₹) before halting (default: 20.0)."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-call HTTP timeout in seconds (default: 120.0)."
    )

    return parser.parse_args()


def main():
    args = parse_args()

    files_arg = args.files
    if isinstance(files_arg, list):
        if len(files_arg) == 0:
            files_arg = None
        elif len(files_arg) == 1:
            files_arg = files_arg[0]

    files_disp = "None"
    if files_arg:
        if isinstance(files_arg, list):
            files_disp = ", ".join(str(f) for f in files_arg)
        else:
            files_disp = str(files_arg)

    print("\n" + "=" * 60)
    print("🤖 BODHAN AUTONOMOUS TASK AGENT")
    print("=" * 60)
    print(f"Task:         {args.task}")
    print(f"Input Files:  {files_disp}")
    print(f"Output Dir:   {args.out}")
    print(f"Max Steps:    {args.max_steps}")
    print(f"Budget Limit: ₹{args.max_cost_inr:.2f}")
    print("=" * 60 + "\n")

    agent = TaskAgent(
        task=args.task,
        files_dir=files_arg,
        out_dir=args.out,
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
        timeout=args.timeout,
    )

    result = agent.run()

    print("\n" + "=" * 60)
    print(f"🎯 EXECUTION FINISHED: Status = {result['status'].upper()}")
    print("=" * 60)
    print(f"Total Steps:   {result['steps']}")
    print(f"Total Cost:    ₹{result['total_cost_inr']:.5f}")
    print(f"Artifacts:     {result['artifacts_count']} files generated in {args.out}/artifacts/")
    print(f"Report:        {args.out}/report.json")
    print(f"Audit Trace:   {args.out}/trace.jsonl")
    print(f"Summary:       {args.out}/result.md")
    print("=" * 60 + "\n")

    # Exit with code 0 on completion or honest failure
    sys.exit(0)


if __name__ == "__main__":
    main()
