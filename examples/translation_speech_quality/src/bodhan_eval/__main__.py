"""Command-line entry point matching the assignment's reviewer command."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import monotonic

from dotenv import load_dotenv

from .calibration import apply_external_labels
from .judge import RUBRIC
from .providers import MICRO_INR, Providers, digest
from .report import write_reports
from .schema import VOICES, load_items
from .workflow import evaluate_item

LOG = logging.getLogger("bodhan_eval.cli")


class JsonLog(logging.Formatter):
    """Write structured, UTF-8 diagnostic events without logging credentials."""

    def format(self, record: logging.LogRecord) -> str:
        """Render one event with stable keys and a separate execution timestamp."""
        return json.dumps(
            {"time": self.formatTime(record), "event": record.getMessage(), **getattr(record, "event_fields", {})},
            ensure_ascii=False,
            sort_keys=True,
        )


def positive(value: str) -> int:
    """Parse an integer count that must be at least one."""
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a positive integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def money(value: str) -> int:
    """Parse a nonnegative INR budget into exact integer micro-rupees."""
    try:
        amount = Decimal(value)
        if not amount.is_finite() or not 0 <= amount <= 1_000_000:
            raise ValueError
        return int(amount * MICRO_INR)
    except (InvalidOperation, ValueError) as error:
        raise argparse.ArgumentTypeError("expected a finite INR amount from 0 to 1000000") from error


def language_set(value: str) -> set[str]:
    """Resolve comma-separated production language codes."""
    selected = {part.strip().lower() for part in value.split(",")}
    unsupported = selected - VOICES.keys()
    if unsupported:
        raise argparse.ArgumentTypeError(f"unsupported language codes {', '.join(sorted(unsupported))}")
    return selected


def args_for_cli() -> argparse.Namespace:
    """Declare the reviewer interface and optional tooling for measured experiments."""
    parser = argparse.ArgumentParser(description="Evaluate Bodhan translations and synthesized speech.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--langs", type=language_set, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--generate", action="store_true", help="generate only missing outputs")
    parser.add_argument("--baseline", type=Path, help="directory containing a previous report")
    parser.add_argument("--calibration", type=Path, help="human-labelled JSONL keyed by id and lang")
    parser.add_argument("--max-cost-inr", type=money, default=50 * MICRO_INR, help="Bodhan spend cap (default 50 INR)")
    parser.add_argument("--cache", type=Path, default=Path(".bodhan-cache/responses.sqlite3"))
    parser.add_argument("--concurrency", type=positive, default=4, help="maximum concurrent model calls")
    parser.add_argument("--limit", type=positive, help="select the first N valid matching rows")
    parser.add_argument("--judge-repeat", type=positive, help="make an extra uncached judge call for N rows")
    parser.add_argument("--no-speech", action="store_true", help="measure translation-only throughput and cost")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    """Execute a bounded evaluation and write reports plus execution telemetry."""
    started = monotonic()
    if args.baseline is not None and args.baseline.resolve() == args.out.resolve():
        raise ValueError("Use a different directory for --out and --baseline.")
    items, invalid = load_items(args.dataset, args.langs)
    invalid_labels = []
    if args.calibration:
        items, invalid_labels = apply_external_labels(items, args.calibration)
    if args.limit:
        items = items[: args.limit]
    ordered_repeats = sorted(items, key=lambda item: digest(f"{item.lang}:{item.id}".encode()))
    repeats = {item.key for item in ordered_repeats[: args.judge_repeat or 0]}
    for kind, rows in (("invalid_item", invalid), ("invalid_label", invalid_labels)):
        for row in rows:
            LOG.info(kind, extra={"event_fields": row})

    async with Providers(args.cache, args.max_cost_inr, args.concurrency) as providers:
        workers = asyncio.Semaphore(args.concurrency)

        async def evaluate(item):
            """Bound total concurrent items as well as the client's HTTP calls."""
            async with workers:
                return await evaluate_item(
                    item,
                    providers,
                    args.dataset.parent,
                    args.out,
                    args.generate,
                    item.key in repeats,
                    enable_speech=not args.no_speech,
                )

        results = await asyncio.gather(*(evaluate(item) for item in items))
        config = {
            "judge_model": providers.judge_model or None,
            "rubric_sha256": digest(RUBRIC.encode()),
            "judge_endpoint_sha256": digest(providers.judge_url.encode()) if providers.judge_url else None,
            "speech_checks_enabled": not args.no_speech,
        }
        write_reports(results, invalid, invalid_labels, args.out, config, args.baseline)
        LOG.info(
            "run_finished",
            extra={
                "event_fields": {
                    "items": len(results),
                    "invalid_rows": len(invalid),
                    "elapsed_seconds": round(monotonic() - started, 3),
                    "bodhan_reserved_or_spent_inr": providers.charged / MICRO_INR,
                    "requests": 0,
                    "cache_hits": 0,
                    **providers.telemetry,
                }
            },
        )
    print(f"Wrote evaluation reports to {args.out.resolve()}")
    return 0 if any(result.translation is not None or result.audio_digest is not None for result in results) else 1


def main() -> int:
    """Load local credentials and turn fatal setup failures into concise CLI errors."""
    args = args_for_cli()
    load_dotenv(Path.cwd() / ".env")
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(args.out / "run.jsonl", mode="w", encoding="utf-8")
        handler.setFormatter(JsonLog())
        logger = logging.getLogger("bodhan_eval")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            return asyncio.run(run(args))
        finally:
            logger.removeHandler(handler)
            handler.close()
    except (OSError, ValueError) as error:
        print(f"bodhan_eval: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("bodhan_eval: interrupted. Successful responses remain cached.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
