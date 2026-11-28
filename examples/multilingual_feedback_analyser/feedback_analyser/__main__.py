"""Command line: `python -m feedback_analyser run sample_data/inbox.csv`."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from .analyse import LexiconAnalyser, LLMAnalyser, load_taxonomy
from .bodhan import BodhanClient, BodhanError
from .cache import DiskCache, NoCache
from .evaluate import evaluate, format_scores, load_records, read_expected
from .ingest import read_manifest
from .languages import TRANSLATE_LANGUAGES
from .pipeline import Options, run
from .report import write_all

RECIPE_DIR = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feedback_analyser", description="Analyse customer feedback written, spoken or photographed in Indian languages.")
    commands = parser.add_subparsers(dest="command", required=True)

    run_cmd = commands.add_parser("run", help="analyse an inbox manifest and write the report")
    run_cmd.add_argument("manifest", type=Path, help="CSV with columns id, channel, text, file, language")
    run_cmd.add_argument("--out", type=Path, default=RECIPE_DIR / "outputs", help="output folder (default: outputs/)")
    run_cmd.add_argument("--taxonomy", type=Path, default=RECIPE_DIR / "taxonomy.json", help="themes, reply templates and domain sentiment words")
    run_cmd.add_argument("--analyser", choices=("lexicon", "llm"), default="lexicon")
    run_cmd.add_argument("--speak", choices=("voice", "all", "none"), default="voice", help="which replies to voice (default: only voice-note feedback)")
    run_cmd.add_argument("--roman-default", default="hi", choices=sorted(TRANSLATE_LANGUAGES - {"en"}), help="language assumed for roman-script text with no hint")
    run_cmd.add_argument("--workers", type=int, default=4, help="parallel items (keep under your key's parallel-request limit)")
    run_cmd.add_argument("--limit", type=int, help="only process the first N rows")
    run_cmd.add_argument("--no-cache", action="store_true", help="call Bodhan even for inputs seen before")
    run_cmd.add_argument("--keep-pii", action="store_true", help="do not mask phone numbers and email addresses in the report")
    run_cmd.add_argument("--dry-run", action="store_true", help="list what would be sent to Bodhan and stop")

    eval_cmd = commands.add_parser("evaluate", help="score a report.json against hand-labelled answers")
    eval_cmd.add_argument("report", type=Path)
    eval_cmd.add_argument("expected", type=Path, help="CSV with columns id, language, sentiment, themes")
    return parser


def command_run(args: argparse.Namespace) -> int:
    items = read_manifest(args.manifest)[: args.limit]
    missing = [item for item in items if item.file and not item.file.exists()]
    if missing:
        for item in missing:
            print(f"{item.id}: file not found: {item.file}", file=sys.stderr)
        return 1
    if args.dry_run:
        channels = Counter(item.channel for item in items)
        print(f"{len(items)} items: {channels['text']} text, {channels['voice']} voice notes (indic-transcribe), {channels['card']} cards (indic-ocr)")
        print("each item is translated to English at most once; each negative item adds two translations and, when voiced, one or more indic-speak calls")
        return 0

    taxonomy = load_taxonomy(args.taxonomy)
    lexicon = LexiconAnalyser(taxonomy)
    analyser = LLMAnalyser(taxonomy, lexicon) if args.analyser == "llm" else lexicon
    cache = NoCache() if args.no_cache else DiskCache(args.out / ".cache")
    client = BodhanClient(cache=cache)
    options = Options(out_dir=args.out, roman_default=args.roman_default, speak=args.speak, keep_pii=args.keep_pii, workers=args.workers)

    records = run(items, client, analyser, taxonomy, options)
    usage = {"calls": dict(client.calls), "cache_hits": dict(client.cache_hits)}
    summary = write_all(args.out, records, taxonomy, usage)
    print(f"analysed {summary['analysed']} of {summary['total']}: {summary['sentiment']['negative']} negative, "
          f"{summary['sentiment']['neutral']} neutral, {summary['sentiment']['positive']} positive")
    print(f"replies drafted {summary['replies']}, needing review {summary['replies_needing_review']}, voiced {summary['replies_voiced']}")
    print(f"Bodhan calls: {usage['calls'] or 'none'}; from cache: {sum(usage['cache_hits'].values())}")
    print(f"report: {args.out / 'report.html'}")
    return 0 if summary["analysed"] else 1


def command_evaluate(args: argparse.Namespace) -> int:
    print(format_scores(evaluate(load_records(args.report), read_expected(args.expected))))
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(RECIPE_DIR / ".env")
    args = build_parser().parse_args(argv)
    try:
        return command_run(args) if args.command == "run" else command_evaluate(args)
    except (BodhanError, ValueError, OSError) as problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
