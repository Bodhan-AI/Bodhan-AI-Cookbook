"""CLI for the Indic task agent; MCP server: python -m indic_task_agent.mcp_server."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from indic_task_agent.agent import _supported_task_guard, run_task
from indic_task_agent.config import ConfigurationError, Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan and run an Indic media task.")
    parser.add_argument("task", nargs="?", help="Plain-language task description")
    parser.add_argument("--input", type=Path, help="Optional image/audio file; the planner receives a local reference")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-budget-inr", type=float)
    parser.add_argument("--dry-run", action="store_true", help="Plan only; make no Bodhan calls")
    args = parser.parse_args()
    task = args.task or input("Describe the task: ").strip()
    try:
        settings = None if _supported_task_guard(task) else Settings.from_env()
        result = run_task(task, settings, args.output_dir, args.input, args.max_steps, args.max_budget_inr, args.dry_run)
    except (ConfigurationError, ValueError) as exc:
        print(f"Configuration/task error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"completed", "planned", "unsupported"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
