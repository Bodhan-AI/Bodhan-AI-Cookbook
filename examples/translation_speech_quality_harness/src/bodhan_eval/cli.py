"""Command-line entry point: python -m bodhan_eval --dataset ... --langs ... --out ..."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bodhan_eval",
        description="Find and explain quality problems in Indic-Translate / Indic-Speak outputs.",
    )
    p.add_argument("--dataset", required=True, type=Path, help="JSONL dataset")
    p.add_argument("--langs", default="", help="comma-separated language codes (default: all)")
    p.add_argument("--out", required=True, type=Path, help="report output directory")
    p.add_argument(
        "--generate",
        action="store_true",
        help="create missing translations/audio with Bodhan models",
    )
    p.add_argument("--baseline", type=Path, help="previous report dir to diff against")
    p.add_argument("--calibration", type=Path, help="human labels JSONL for calibration")
    p.add_argument(
        "--thresholds",
        type=Path,
        help="calibration.json from an earlier run: apply its thresholds instead of "
        "re-calibrating (labelled items are then a held-out evaluation)",
    )
    p.add_argument("--max-cost-inr", type=float, default=None, help="hard budget for paid calls")
    # extras
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(os.environ.get("BODHAN_EVAL_CACHE_DIR", ".bodhan_cache")),
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="never call a model; use cached outputs only (cache misses are logged)",
    )
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument(
        "--speech-sample",
        type=float,
        default=None,
        help="max fraction (0-1] or count (>1) of items to run speech checks on",
    )
    p.add_argument("--no-judge", action="store_true", help="skip the LLM judge")
    p.add_argument(
        "--judge-repeats", type=int, default=3, help="re-judgements on the consistency sample"
    )
    p.add_argument(
        "--voice",
        default=None,
        help="Indic-Speak voice for --generate (default: per-language voice in languages.yaml)",
    )
    p.add_argument("--seed", type=int, default=13, help="seed for deterministic sampling")
    p.add_argument(
        "--fail-on-regression",
        type=float,
        default=None,
        help="CI mode: exit 3 if quality score drops by more than this vs baseline",
    )
    p.add_argument("--log-level", default="INFO")
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    from bodhan_eval.pipeline import run  # imported late so --help stays fast

    return run(args)
