"""Command-line interface. Required flags are fixed by the assignment; extras are allowed."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bodhan_agent", description="Run a plain-language task with Bodhan tools.")
    p.add_argument("--task", required=True, help="Plain-language task description.")
    p.add_argument("--files", nargs="*", default=[], help="Input file(s) or folder(s).")
    p.add_argument("--out", required=True, help="Output folder for this run.")
    p.add_argument("--max-steps", type=int, default=30, help="Maximum agent steps (default 30).")
    p.add_argument("--max-cost-inr", type=float, default=20.0, help="Maximum spend in rupees (default 20).")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"bodhan_agent: not implemented yet (scaffold). task={args.task!r}", file=sys.stderr)
    return 2
