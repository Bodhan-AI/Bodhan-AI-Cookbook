"""CLI entry point.

The required interface is fixed:

    python -m bodhan_dub --input video.mp4 --langs hi,ta,bn --out ./out \
        [--glossary glossary.yaml] [--voices voices.yaml] [--code-mixed]

Extra flags (--mock, --concurrency, --asr-model, --vad, --resume, --log-level)
are optional and never change the required ones. Exit codes: 0 all languages ok,
1 at least one language failed (partial outputs + report still written), 2 config error.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

import httpx
from tenacity import RetryError

from .config import ConfigError, load_settings
from .logging_setup import get_logger, setup_logging
from .pipeline.orchestrator import run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bodhan_dub",
        description="Dub English videos into Indian languages with Bodhan's Indic models.",
    )
    p.add_argument("--input", required=True, help="Path to the English source video (mp4/mov/mkv/webm)")
    p.add_argument("--langs", required=True, help="Comma-separated target language codes, e.g. hi,ta,bn")
    p.add_argument("--out", default="./out", help="Output directory (default: ./out)")
    p.add_argument("--glossary", default=None, help="Optional glossary YAML to enforce terminology")
    p.add_argument("--voices", default=None, help="Optional voices YAML overriding the voice registry")
    p.add_argument("--code-mixed", action="store_true", help="Use code-mixed targets (e.g. Hinglish)")
    # --- optional behaviour knobs ---
    p.add_argument("--mock", action="store_true", help="Run offline with deterministic fixtures (no keys/credit)")
    p.add_argument("--burn-subs", action="store_true", help="Also write video.<lang>.subbed.mp4 with hardcoded subs")
    p.add_argument("--sub-font", default="", help="Font for burned-in subtitles (default: per-script system font)")
    p.add_argument("--concurrency", type=int, default=2, help="Max concurrent Bodhan API calls")
    p.add_argument("--rate-interval", type=float, default=1.0, help="Seconds between request starts (adapts up on 429)")
    p.add_argument("--asr-model", default="indic-transcribe", help="Indic-Transcribe model id (Core/Flex)")
    p.add_argument("--vad", default="energy", choices=["energy", "webrtc", "silero"], help="VAD backend")
    p.add_argument("--resume", action="store_true", help="Reuse cached stage results from a prior run")
    p.add_argument("--log-level", default="INFO", help="Logging level (DEBUG/INFO/WARNING/ERROR)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    log = get_logger("cli")
    try:
        settings = load_settings(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    try:
        code = asyncio.run(run(settings))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as e:  # unexpected top-level failure
        log.error("fatal", exc_info=True, extra={"event": "fatal"})
        underlying = e
        if isinstance(e, RetryError):
            with contextlib.suppress(Exception):
                underlying = e.last_attempt.exception()
        if isinstance(underlying, httpx.HTTPStatusError) and underlying.response.status_code == 429:
            print("fatal: the Bodhan API rate-limited this run (HTTP 429). "
                  "Re-run with a lower --concurrency (e.g. --concurrency 1).", file=sys.stderr)
        else:
            print(f"fatal: {e}", file=sys.stderr)
        return 1
    if code != 0:
        print("completed with failures — see report.json", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
