"""CLI entry point: python -m bodhan_agent --task "..." --files path/ --out ./out"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .run import RunConfig, run_task


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bodhan_agent", description=__doc__)
    p.add_argument("--task", required=True, help="plain-language task description")
    p.add_argument("--files", required=True, nargs="+", help="input file(s) or directory")
    p.add_argument("--out", required=True, help="output directory for this run")
    p.add_argument("--max-steps", type=int, default=30)
    p.add_argument("--max-cost-inr", type=float, default=20.0)
    p.add_argument("--call-timeout-s", type=float, default=120.0, help="per tool-call timeout")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = RunConfig(
        task=args.task,
        files=[Path(f) for f in args.files],
        out=Path(args.out),
        max_steps=args.max_steps,
        max_cost_inr=args.max_cost_inr,
        call_timeout_s=args.call_timeout_s,
    )
    report = run_task(cfg)
    print(f"status={report['status']} cost_inr={report['total_cost_inr']:.2f} out={cfg.out}")
    return 0 if report["status"] in {"completed", "impossible"} else 1


if __name__ == "__main__":
    sys.exit(main())
