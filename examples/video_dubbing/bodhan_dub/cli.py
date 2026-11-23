"""Command line entry point.

The four flags required by the brief are fixed and must not change:

    --input  --langs  --out  [--glossary] [--voices] [--code-mixed]

Everything else is additive and optional.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from . import __version__, languages
from .config import Settings, load_settings
from .errors import ConfigError
from .log import event, setup_logging
from .pipeline import STAGES, RunArgs, run

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bodhan_dub",
        description="Dub an English video into Indian languages with Bodhan Indic models.",
    )
    parser.add_argument("--input", required=True, type=Path, help="video file (MP4, MOV, MKV or WebM)")
    parser.add_argument("--langs", required=True, help="target languages, comma separated, e.g. hi,ta,bn")
    parser.add_argument("--out", type=Path, default=Path("./out"), help="output directory")
    parser.add_argument("--glossary", type=Path, help="YAML file of fixed term translations")
    parser.add_argument("--voices", type=Path, help="YAML file mapping speakers or languages to voices")
    parser.add_argument("--code-mixed", action="store_true", help="keep English terms inside the dub")
    parser.add_argument(
        "--burn-subtitles",
        action="store_true",
        help="render subtitles into the picture instead of embedding a subtitle track "
        "(requires an ffmpeg built with libass)",
    )
    parser.add_argument(
        "--stage",
        choices=("all", *STAGES),
        default="all",
        help="stop after a named stage; the default runs the whole pipeline",
    )
    parser.add_argument("--cache-dir", type=Path, help="where model responses are cached")
    parser.add_argument("--force", action="store_true", help="ignore cache reads and call the API again")
    parser.add_argument("--log-level", choices=LOG_LEVELS, help="console log level")
    parser.add_argument("--version", action="version", version=f"bodhan_dub {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        langs = languages.parse_langs(args.langs)
    except ValueError as exc:
        parser.error(str(exc))

    if not args.input.exists():
        parser.error(f"--input does not exist: {args.input}")
    if not args.input.is_file():
        parser.error(f"--input is not a file: {args.input}")

    try:
        settings = _settings_with_overrides(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(settings.log_level, args.out / "run.log")

    event(
        logger,
        "run.start",
        stage="pipeline",
        input=str(args.input),
        langs=",".join(langs),
        stage_requested=args.stage,
        code_mixed=args.code_mixed,
        version=__version__,
    )

    # Fail before any paid work if the key for the first stage is missing.
    try:
        settings.key_for("transcribe")
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    run_args = RunArgs(
        input=args.input,
        langs=langs,
        out=args.out,
        stage=args.stage,
        glossary=args.glossary,
        voices=args.voices,
        code_mixed=args.code_mixed,
        force=args.force,
        burn_subtitles=args.burn_subtitles,
    )
    return run(run_args, settings, logger)


def _settings_with_overrides(args: argparse.Namespace) -> Settings:
    settings = load_settings()
    changes = {}
    if args.cache_dir:
        changes["cache_dir"] = args.cache_dir
    if args.log_level:
        changes["log_level"] = args.log_level
    return dataclasses.replace(settings, **changes) if changes else settings
