"""Command-line entry point: `python -m bodhan_dub --input video.mp4 --langs hi,ta,bn --out ./out`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from . import logs, media, pipeline
from .config import Settings
from .languages import LANGUAGE_NAMES, TARGET_LANGS

log = logging.getLogger("bodhan_dub")

# Exit codes: 0 all languages dubbed, 1 at least one language failed, 2 bad usage or setup.
EXIT_OK, EXIT_PARTIAL, EXIT_USAGE = 0, 1, 2


def parse_langs(value: str) -> list[str]:
    """Split "hi, ta,bn" into ["hi", "ta", "bn"], de-duplicated, rejecting unknown codes."""
    langs: list[str] = []
    for raw in value.split(","):
        code = raw.strip()
        if not code:
            continue
        if code == "en":
            raise argparse.ArgumentTypeError("'en' is the source language, not a dub target")
        if code not in TARGET_LANGS:
            supported = ", ".join(f"{c} ({LANGUAGE_NAMES[c]})" for c in TARGET_LANGS)
            raise argparse.ArgumentTypeError(f"unsupported language {code!r}; choose from: {supported}")
        if code not in langs:
            langs.append(code)
    if not langs:
        raise argparse.ArgumentTypeError("give at least one target language, e.g. --langs hi,ta,bn")
    return langs


def existing_video(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"no such file: {value}")
    if path.suffix.lower() not in media.SUPPORTED_SUFFIXES:
        raise argparse.ArgumentTypeError(
            f"unsupported container {path.suffix!r}; use one of {', '.join(sorted(media.SUPPORTED_SUFFIXES))}"
        )
    return path


def existing_file(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"no such file: {value}")
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bodhan_dub", description="Dub an English video into Indian languages.")
    p.add_argument("--input", required=True, type=existing_video, help="English video: MP4, MOV, MKV or WebM")
    p.add_argument("--langs", required=True, type=parse_langs, help="comma-separated targets, e.g. hi,ta,bn")
    p.add_argument("--out", required=True, type=Path, help="output folder")
    p.add_argument("--glossary", type=existing_file, help="YAML of terms to keep fixed across translations")
    p.add_argument("--voices", type=existing_file, help="YAML mapping languages/speakers to Indic-Speak voices")
    p.add_argument("--code-mixed", action="store_true", help="code-mixed targets (e.g. Hinglish) via Indic-Translate")
    p.add_argument("--ascii-digits", action="store_true",
                   help="send digits to Indic-Speak as written, which it reads in English; by default they are "
                        "sent in the language's own digits so numbers are spoken in that language")
    p.add_argument("--max-parallel", type=int, default=4, help="max concurrent Bodhan API calls (default 4)")
    p.add_argument("--cache-dir", type=Path, help="model-call cache, reused across runs (default <out>/.cache)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(find_dotenv(usecwd=True))  # the .env next to where the user runs the command
    args.out.mkdir(parents=True, exist_ok=True)
    logs.configure(args.log_level, args.out / "run.log")

    settings = Settings.from_env()
    if missing := settings.missing():
        log.error("missing configuration: %s (see .env.example)", ", ".join(missing))
        return EXIT_USAGE
    opts = pipeline.RunOptions(input=args.input, langs=args.langs, out=args.out,
                               cache_dir=args.cache_dir or args.out / ".cache", max_parallel=args.max_parallel,
                               glossary=args.glossary, voices=args.voices,
                               code_mixed=args.code_mixed, ascii_digits=args.ascii_digits)
    try:
        result = pipeline.run(opts, settings)
    except (media.MediaError, pipeline.PipelineError) as exc:
        log.error("%s", exc)
        return EXIT_USAGE
    if result.failed:
        log.error("some languages failed: %s (see report.json)", ", ".join(result.failed))
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
