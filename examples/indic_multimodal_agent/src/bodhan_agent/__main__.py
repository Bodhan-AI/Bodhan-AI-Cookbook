"""Command-line interface entrypoint for Bodhan Task Agent.

Usage:
python -m bodhan_agent --task "..." --files DIR --out ./out [--max-steps 30] [--max-cost-inr 20]
"""

import argparse
import json
import sys
from pathlib import Path
from bodhan_agent.agent import BodhanTaskAgent


def parse_args():
    parser = argparse.ArgumentParser(description="Bodhan Task Agent CLI")
    parser.add_argument("--task", type=str, required=True, help="Task instruction in plain English")
    parser.add_argument("--files", type=str, required=True, help="File or directory of input files")
    parser.add_argument("--out", type=str, required=True, help="Output directory for artifacts and reports")
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum execution steps budget (default: 30)")
    parser.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum cost budget in INR (default: ₹20.0)")
    return parser.parse_args()


def main():
    try:
        args = parse_args()
    except SystemExit as se:
        sys.exit(se.code)
    except Exception as e:
        print(f"[ERROR] Failed to parse arguments: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        agent = BodhanTaskAgent(
            task=args.task,
            files_dir=args.files,
            out_dir=args.out,
            max_steps=args.max_steps,
            max_cost_inr=args.max_cost_inr,
        )
        success = agent.execute()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"[ERROR] Agent execution failure: {e}", file=sys.stderr)
        try:
            out_p = Path(args.out)
            out_p.mkdir(parents=True, exist_ok=True)
            rep_p = out_p / "report.json"
            if not rep_p.exists():
                with open(rep_p, "w", encoding="utf-8") as f:
                    json.dump({
                        "status": "failed",
                        "task": getattr(args, "task", "unknown"),
                        "total_steps": 0,
                        "total_cost_inr": 0.0,
                        "duration_seconds": 0.0,
                        "verification_results": [],
                        "artifacts": [],
                        "error": str(e),
                    }, f, indent=2)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
