"""Run `python -m bodhan_agent --task ... --files ... --out ...`."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bodhan_shared.config import load_dotenv

from .runner import AgentRunner


def main() -> int:
    p = argparse.ArgumentParser(description="Bodhan tool-driven language task agent")
    p.add_argument("--task", required=True, help="Natural-language instruction")
    p.add_argument("--files", required=True, help="File or directory containing task inputs")
    p.add_argument("--out", required=True, help="Output directory")
    p.add_argument("--max-steps", type=int, default=30)
    p.add_argument("--max-cost-inr", type=float, default=20)
    args = p.parse_args()
    if not 1 <= args.max_steps <= 200 or args.max_cost_inr < 0:
        p.error("max-steps must be 1–200 and max-cost-inr nonnegative")
    load_dotenv()
    run = AgentRunner(task=args.task, files=args.files, out=args.out, max_steps=args.max_steps,
                      max_cost_inr=args.max_cost_inr).run()
    print(json.dumps({"status": run["status"], "steps": run["steps"],
                      "estimated_bodhan_cost_inr": run["estimated_bodhan_cost_inr"],
                      "report": str(Path(args.out).resolve() / "report.json")}, indent=2))
    return 0 if run["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
