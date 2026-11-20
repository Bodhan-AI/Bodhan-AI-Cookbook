"""Command-line entry point for the Bodhan video dubbing recipe."""

from __future__ import annotations

import argparse
import logging
import math
import os
import sqlite3
import sys
from pathlib import Path

from .budget import Budget, BudgetError
from .config import VOICES, read_glossary, read_voices, settings
from .pipeline import dub
from .providers import Provider, ProviderError


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("expected a finite number")
    return number


def _languages(value: str) -> list[str]:
    languages = [code.strip().lower() for code in value.split(",")]
    unknown = set(languages) - VOICES.keys()
    if unknown:
        raise argparse.ArgumentTypeError(f"unsupported codes {sorted(unknown)}. Choose from {', '.join(VOICES)}")
    if len(set(languages)) != len(languages):
        raise argparse.ArgumentTypeError("each language may occur only once")
    return languages


def main(argv: list[str] | None = None) -> int:
    state = Path(os.getenv("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "bodhan_dub"
    parser = argparse.ArgumentParser(description="Dub English video with Bodhan speech and translation models")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--langs", type=_languages, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--glossary", type=Path, help="YAML mapping of English terms to target-language renderings")
    parser.add_argument("--voices", type=Path, help="YAML mapping of language codes to fixed Indic-Speak voice names")
    parser.add_argument("--background-stem", type=Path, help="optional clean music/effects stem on the source timeline")
    parser.add_argument("--state-dir", type=Path, default=Path(os.getenv("BODHAN_DUB_STATE_DIR") or state),
                        help="shared spend ledger and response cache across submissions and resumes")
    parser.add_argument("--budget-inr", type=_number, default=10.0, help="maximum cumulative modeled spend in INR")
    parser.add_argument("--prior-spend-inr", type=_number, default=0.02, help="existing submission spend estimate")
    parser.add_argument("--workers", type=int, default=3, help="maximum concurrent language workers and API calls")
    parser.add_argument("--excerpt-seconds", type=_number, help="process up to 45 seconds from a valid full input")
    parser.add_argument("--preflight-only", action="store_true", help="checkpoint translation and estimate first-pass cost")
    parser.add_argument("--require-complete-translation", action="store_true",
                        help="require all requested source and translation results before any synthesis")
    parser.add_argument("--skip-translation-review", action="store_true", help="skip optional LLM translation judgments")
    parser.add_argument("--code-mixed", action="store_true", help="reserved for a later version")
    parser.add_argument("--verbose", action="store_true", help="log acceptance and fallback decisions")
    args = parser.parse_args(argv)
    if args.code_mixed:
        parser.error("code-mixed dubbing is unavailable in this recipe")
    if not 0 < args.budget_inr <= 10 or not 0 <= args.prior_spend_inr <= args.budget_inr:
        parser.error("budget must be within (0, 10] INR and prior spend between zero and the budget")
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be between 1 and 8")
    if args.excerpt_seconds is not None and not 1 <= args.excerpt_seconds <= 45:
        parser.error("--excerpt-seconds must be between 1 and 45")
    if args.background_stem is not None and not args.background_stem.is_file():
        parser.error("--background-stem must be an existing audio file")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        voices = read_voices(args.voices)
        glossary = read_glossary(args.glossary)
        args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        budget = Budget(args.state_dir / "ledger.sqlite3", cap_inr=args.budget_inr, prior_inr=args.prior_spend_inr)
        provider = Provider(budget, concurrency=args.workers, **settings())
        return dub(args, provider, voices, glossary)
    except (ValueError, OSError, ProviderError, BudgetError, sqlite3.Error) as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
