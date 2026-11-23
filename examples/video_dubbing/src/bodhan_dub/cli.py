"""
Command-line interface for bodhan_dub.
Parses flags, runs preflight checks, handles signals for graceful exit,
and orchestrates the dubbing pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from bodhan_dub.config import Settings
from bodhan_dub.languages import LanguageRegistry
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.manifest import RunManifest
from bodhan_dub.preflight import PreflightError, run_preflight_checks


def build_parser() -> argparse.ArgumentParser:
    """Constructs the command-line argument parser matching the exact specification."""
    parser = argparse.ArgumentParser(
        prog="bodhan_dub",
        description="Autonomous English to Indic video dubbing pipeline using Bodhan AI models.",
    )

    # Core required options
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to source video file (MP4, MOV, MKV, or WebM, 1-15 min duration)",
    )
    parser.add_argument(
        "--langs",
        type=str,
        required=True,
        help="Comma-separated target Indic language codes (e.g. 'hi,ta,bn')",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("./out"),
        help="Output directory path (default: ./out)",
    )

    # Optional resources
    parser.add_argument(
        "--glossary",
        type=Path,
        default=None,
        help="Path to custom glossary YAML file",
    )
    parser.add_argument(
        "--voices",
        type=str,
        default=None,
        help="Custom voices mapping: 'lang:voice' or path to YAML file",
    )
    parser.add_argument(
        "--code-mixed",
        action="store_true",
        default=False,
        help="Generate code-mixed Indic output preserving English technical terms",
    )

    # Extra operational flags per spec
    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=None,
        help="Budget ceiling in INR; pipeline stops cleanly if reached",
    )
    parser.add_argument(
        "--estimate-only",
        action="store_true",
        default=False,
        help="Print estimated cost breakdown in INR and exit without processing",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Maximum concurrent parallel language workers (default: 2)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="Disable content-addressed disk cache",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume execution from existing run manifest checkpoints",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        default=False,
        help="Retain intermediate extracted audio and work directory stems",
    )
    parser.add_argument(
        "--separate-music",
        action="store_true",
        default=False,
        help="Perform music separation (e.g. Demucs) to isolate background audio",
    )
    parser.add_argument(
        "--speaker-voices",
        choices=["single", "auto"],
        default="single",
        help="Voice assignment strategy: 'single' (default) or 'auto' (pitch-based heuristic)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging verbosity (default: INFO)",
    )

    return parser


async def async_main(
    args: argparse.Namespace,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> int:
    """Async orchestration entry point."""
    run_id = f"run_{int(time.time())}"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    log_file = out_dir / "run.log.jsonl"
    logger = PipelineLogger(
        run_id=run_id,
        log_file=log_file,
        level=getattr(__import__("logging"), args.log_level),
    )

    logger.info(f"Starting bodhan_dub run_id={run_id}")
    logger.info(f"Input: {args.input}, Languages: {args.langs}, Output dir: {out_dir}")

    # B3: Wrap Settings() construction so invalid settings give exit 2, not a traceback
    try:
        settings = Settings()
    except Exception as e:
        logger.error(f"Configuration / Settings error: {e}", stage="preflight")
        print(f"\n[ERROR] Invalid configuration or missing settings: {e}", file=sys.stderr)
        return 2

    # C2: Reject --max-cost-inr <= 0 with exit 2
    if args.max_cost_inr is not None and args.max_cost_inr <= 0.0:
        logger.error(f"--max-cost-inr must be > 0, got {args.max_cost_inr}", stage="preflight")
        print(f"\n[ERROR] --max-cost-inr must be > 0, got {args.max_cost_inr}", file=sys.stderr)
        return 2

    registry = LanguageRegistry()

    # Parse and validate target languages
    target_langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()]

    # Run Preflight validation
    try:
        media_info, estimates = run_preflight_checks(
            input_path=args.input,
            target_langs=target_langs,
            settings=settings,
            registry=registry,
            estimate_only=args.estimate_only,
            voices_arg=args.voices,
        )
        from bodhan_dub.preflight import validate_voices_mapping
        custom_voices = validate_voices_mapping(args.voices, registry)
    except PreflightError as e:
        logger.error(f"Preflight check failed: {e}", stage="preflight")
        print(f"\n[ERROR] Preflight check failed: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        logger.error(f"Language validation failed: {e}", stage="preflight")
        print(f"\n[ERROR] Language validation failed: {e}", file=sys.stderr)
        return 2

    # If --estimate-only, print table and exit cleanly with code 0
    if args.estimate_only:
        print("\n" + "=" * 50)
        print("BODHAN DUBBING PIPELINE — COST ESTIMATE")
        print("=" * 50)
        print(f"Input duration:     {estimates['duration_minutes']:.2f} minutes")
        print(f"Target languages:   {estimates['languages_count']} ({', '.join(target_langs)})")
        print(f"ASR (Shared + QA):  ₹{estimates['asr_estimate_inr']:.2f}")
        print(f"Translation:        ₹{estimates['translate_estimate_inr']:.2f}")
        print(f"Speech Synthesis:   ₹{estimates['tts_estimate_inr']:.2f}")
        print("-" * 50)
        print(f"Total Estimate:     ₹{estimates['total_estimate_inr']:.2f} INR")
        print("=" * 50)
        return 0

    # Budget ceiling check
    if args.max_cost_inr and estimates["total_estimate_inr"] > args.max_cost_inr:
        logger.error(
            f"Estimated cost (₹{estimates['total_estimate_inr']:.2f}) exceeds budget ceiling (₹{args.max_cost_inr:.2f})",
            stage="preflight",
        )
        print(
            f"\n[ERROR] Estimated pipeline cost (₹{estimates['total_estimate_inr']:.2f}) exceeds "
            f"--max-cost-inr (₹{args.max_cost_inr:.2f}). Please specify fewer languages or increase budget.",
            file=sys.stderr,
        )
        return 2

    # Manifest initialization for resume tracking
    manifest_path = out_dir / "manifest.json"
    manifest = RunManifest.load(manifest_path) if args.resume else None
    if not manifest:
        manifest = RunManifest(
            run_id=run_id,
            input_file=str(args.input),
            target_langs=target_langs,
        )
        manifest.save(manifest_path)

    # Initialize Pipeline Orchestrator
    from bodhan_dub.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=out_dir,
        no_cache=args.no_cache,
        transport=transport,
        glossary_path=args.glossary,
        code_mixed=args.code_mixed,
        keep_work=args.keep_work,
        concurrency=args.concurrency,
        resume=args.resume,
        separate_music=args.separate_music,
        speaker_voices=args.speaker_voices,
        custom_voices=custom_voices,
    )

    try:
        exit_code = await orchestrator.run(
            video_path=args.input,
            target_langs=target_langs,
            max_cost_inr=args.max_cost_inr,
            custom_voices=custom_voices,
        )
        return exit_code
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.warning(
            "Pipeline interrupted by user (SIGINT/KeyboardInterrupt). Flushing partial outputs.",
            stage="cli",
        )
        orchestrator.flush_partial_state(args.input)
        raise
    except Exception as e:
        logger.error(f"Unexpected pipeline exception: {e}", stage="cli")
        orchestrator.flush_partial_state(args.input)
        raise
    finally:
        await orchestrator.close()


def main() -> None:
    """
    B9 & B20: Synchronous CLI wrapper handling argument parsing, exact exit codes,
    and POSIX/Windows signal trapping (canceling tasks, flushing state, exit 130).
    """
    import signal

    parser = build_parser()
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _cancel_all_tasks():
        for task in asyncio.all_tasks(loop):
            task.cancel()

    # Install cross-platform interrupt handlers
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _cancel_all_tasks)
            except NotImplementedError:
                signal.signal(sig, lambda _s, _f: _cancel_all_tasks())
    else:
        signal.signal(signal.SIGINT, lambda _s, _f: _cancel_all_tasks())
        signal.signal(signal.SIGTERM, lambda _s, _f: _cancel_all_tasks())

    try:
        exit_code = loop.run_until_complete(async_main(args))
        sys.exit(exit_code)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(
            "\n[INFO] Interrupted by user (SIGINT). Flushed partial manifest and report.",
            file=sys.stderr,
        )
        sys.exit(130)
    except PreflightError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"\n[FATAL] Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        loop.close()


if __name__ == "__main__":
    main()
