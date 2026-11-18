#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

# Load .env so API keys are available before any Bodhan/LLM client is built.
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from bodhan_mcp.tools import inspect_media  # noqa: E402
from bodhan_mcp.client import InrLedger  # noqa: E402
from .planner import TaskPlanner  # noqa: E402
from .mcp_bridge import MCPBridge  # noqa: E402
from .reporter import Reporter  # noqa: E402


async def expand_inputs(files: list[str]) -> list[str]:
    """Expand directories to sorted non-hidden files."""
    expanded = []
    for f in files:
        path = Path(f)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {f}")
        if path.is_dir():
            for item in sorted(path.iterdir()):
                if not item.name.startswith(".") and item.is_file():
                    expanded.append(str(item))
        else:
            expanded.append(str(path))
    return expanded


async def preflight_cost_check(
    files: list[str], ledger: InrLedger
) -> tuple[float, list[dict]]:
    """Run inspect_media on all files to compute minimum cost floor."""
    total_min_cost = 0.0
    inspect_results = []
    for f in files:
        result = await inspect_media(f)
        if "error" in result:
            raise RuntimeError(
                f"Inspection failed for {f}: {result.get('message')}"
            )
        min_cost = result.get("min_cost_inr", 0.0)
        total_min_cost += min_cost
        inspect_results.append({"file": f, **result})
    return total_min_cost, inspect_results


async def run_agent(
    task: str,
    files: list[str],
    out_dir: Path,
    max_steps: int,
    max_cost_inr: float,
) -> dict[str, Any]:
    """Main agent execution loop."""
    start_time = time.time()
    ledger = InrLedger(max_cost=max_cost_inr)
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    reporter = Reporter(out_dir, artifacts_dir, ledger, max_steps, max_cost_inr)
    bridge = MCPBridge()
    planner = TaskPlanner(bridge, reporter, ledger, max_steps, max_cost_inr)

    try:
        await bridge.start()
        result = await planner.execute(task, files)
        await reporter.finalize(result, time.time() - start_time)
        return result
    finally:
        await bridge.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Unattended CLI task agent for Bodhan AI"
    )
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument(
        "--files", nargs="+", required=True,
        help="Input files or directories",
    )
    parser.add_argument("--out", default="./out", help="Output directory")
    parser.add_argument(
        "--max-steps", type=int, default=30,
        help="Maximum planning steps",
    )
    parser.add_argument(
        "--max-cost-inr", type=float, default=20.0,
        help="Maximum cost in INR",
    )
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()

    try:
        expanded_files = asyncio.run(expand_inputs(args.files))
    except FileNotFoundError as e:
        out_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "status": "failed",
            "error": "MISSING_INPUT_FILE",
            "message": str(e),
            "steps_used": 0,
            "max_steps": args.max_steps,
            "total_cost_inr": 0.0,
            "max_cost_inr": args.max_cost_inr,
            "elapsed_sec": 0.0,
            "assumptions": [],
            "verification_results": {},
            "artifacts": [],
            "missed_targets": [str(e)],
        }
        (out_dir / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(f"MISSING_INPUT_FILE: {e}", file=sys.stderr)
        return 1

    try:
        ledger = InrLedger(max_cost=args.max_cost_inr)
        min_cost, _ = asyncio.run(
            preflight_cost_check(expanded_files, ledger)
        )

        if min_cost > args.max_cost_inr:
            out_dir.mkdir(parents=True, exist_ok=True)
            report = {
                "status": "budget_exceeded",
                "error": "BUDGET_EXCEEDED",
                "message": (
                    f"Minimum cost floor "
                    f"\u20b9{min_cost:.4f} exceeds "
                    f"max cost \u20b9{args.max_cost_inr:.2f}"
                ),
                "steps_used": 0,
                "max_steps": args.max_steps,
                "total_cost_inr": 0.0,
                "max_cost_inr": args.max_cost_inr,
                "elapsed_sec": 0.0,
                "assumptions": [],
                "verification_results": {},
                "artifacts": [],
                "missed_targets": [
                    f"Budget exceeded: "
                    f"min cost {min_cost:.4f} > "
                    f"max {args.max_cost_inr:.2f}"
                ],
            }
            (out_dir / "report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            print(
                f"BUDGET_EXCEEDED: Minimum cost floor "
                f"\u20b9{min_cost:.4f} exceeds "
                f"max cost \u20b9{args.max_cost_inr:.2f}",
                file=sys.stderr,
            )
            return 1

        result = asyncio.run(run_agent(
            args.task,
            expanded_files,
            out_dir,
            args.max_steps,
            args.max_cost_inr,
        ))
        return 0 if result.get("status") == "completed" else 1

    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as e:
        out_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "status": "failed",
            "error": "EXECUTION_ERROR",
            "message": str(e),
            "steps_used": 0,
            "max_steps": args.max_steps,
            "total_cost_inr": 0.0,
            "max_cost_inr": args.max_cost_inr,
            "elapsed_sec": 0.0,
            "assumptions": [],
            "verification_results": {},
            "artifacts": [],
            "missed_targets": [str(e)],
        }
        (out_dir / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(f"EXECUTION_ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
