"""python -m bodhan_eval --dataset data.jsonl --langs hi,ta,bn --out ./report [options]"""

import argparse
import os
import sys

from . import pipeline
from .dataset import DatasetError


def load_env_file(path: str):
    """Minimal KEY=VALUE reader. Existing environment variables win."""
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bodhan_eval", description="Find and locate errors in Indic-Translate and Indic-Speak outputs.")
    p.add_argument("--dataset", required=True, help="JSONL file, one item per line")
    p.add_argument("--langs", required=True, help="comma-separated language codes, e.g. hi,ta,bn")
    p.add_argument("--out", required=True, help="report folder")
    p.add_argument("--generate", action="store_true", help="create missing translations and audio with Bodhan models")
    p.add_argument("--baseline", help="previous report folder to diff against")
    p.add_argument("--calibration", help="JSONL of human-labelled items, or a filled review_queue.csv")
    p.add_argument("--fail-on-regression", type=int, metavar="N",
                   help="with --baseline: exit 1 when more than N items gain a major or critical flag")
    p.add_argument("--skip", metavar="CHECKS", help="comma-separated checks to switch off, for ablations: "
                   "back_translation, regen_diff, audio_stats, transcript, judge")
    p.add_argument("--max-cost-inr", type=float, default=None, help="stop paid calls before this many rupees")
    p.add_argument("--cache-dir", default=".bodhan_eval_cache", help="where model outputs are cached")
    p.add_argument("--concurrency", type=int, default=4, help="parallel calls per model")
    p.add_argument("--env-file", help="read KEY=VALUE settings from this file (existing env wins)")
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parser().parse_args(argv)
    if args.env_file:
        load_env_file(args.env_file)
    try:
        return pipeline.run(args)
    except DatasetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: cannot write report: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
