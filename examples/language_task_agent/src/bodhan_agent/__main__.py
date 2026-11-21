"""Required command: python -m bodhan_agent --task ... --files ... --out ..."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from bodhan_core.runtime import Limits, Run, ToolError, decimal
from .runner import execute


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Bounded non-interactive Bodhan language-task agent"
    )
    parser.add_argument("--task", required=True)
    parser.add_argument("--files", required=True, nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-cost-inr", default="20")
    parser.add_argument("--call-timeout", type=float, default=45)
    parser.add_argument("--max-seconds", type=float, default=300)
    parser.add_argument(
        "--execution-window",
        type=float,
        help="Optional cooperative window in seconds; does not reset total task budgets.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume validated private JSON in the same --out folder.",
    )
    parser.add_argument(
        "--input-note",
        action="append",
        default=[],
        help="Caller-supplied provenance disclosure included verbatim in assumptions.",
    )
    parser.add_argument(
        "--carry-budget-from",
        help="Explicit post-terminal correction lineage: carry earlier report active time, cost and steps. Not a state resume.",
    )
    args = parser.parse_args()
    try:
        supplied = [Path(value).resolve() for value in args.files]
        parents = [p if p.is_dir() else p.parent for p in supplied]
        root = Path(os.path.commonpath(parents))
        limits = Limits(
            decimal(args.max_cost_inr), args.max_steps, args.call_timeout, args.max_seconds
        )
        if args.resume:
            from .checkpoint import restore

            run = restore(root, Path(args.out), limits, args.task)
        else:
            run = Run(root, Path(args.out), limits)
        if args.execution_window is not None:
            import math

            if (
                not math.isfinite(args.execution_window)
                or args.execution_window <= args.call_timeout + 3
            ):
                raise ToolError(
                    "invalid_window",
                    "Execution window must exceed call timeout by at least three seconds.",
                )
            run.execution_window = args.execution_window
        run.input_files = []
        for path in supplied:
            if not path.exists():
                report = run.write_report("failed", f"missing_file: Input not found: {path.name}")
                print(report["explanation"])
                return 2
            run.input_files.extend(sorted(path.rglob("*")) if path.is_dir() else [path])
        if args.resume:
            from .checkpoint import inputs_fingerprint
            import json

            stored = json.loads((run.output / ".continuation.json").read_text())
            if inputs_fingerprint(run) != stored["inputs"]:
                raise ToolError("invalid_checkpoint", "Supplied input set changed since pause.")
        from .checkpoint import run_context
        import json

        if not args.resume:
            (run.output / "run_context.json").write_text(json.dumps(run_context(run)))
        if args.carry_budget_from:
            if args.resume:
                raise ToolError(
                    "invalid_continuation",
                    "Carry only when creating a correction run, not on resume.",
                )
            from .checkpoint import carry_budget

            carry_budget(run, Path(args.carry_budget_from))
        for note in args.input_note:
            if note not in run.assumptions:
                run.assumptions.append(note)
        report = asyncio.run(execute(args.task, run))
        print(f"{report['status']}: {Path(args.out) / 'result.md'}")
        return 0 if report["status"] == "completed" else 2
    except (ToolError, OSError, ValueError) as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
