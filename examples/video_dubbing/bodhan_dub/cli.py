import argparse
import logging
import sys
from pathlib import Path

import yaml

from bodhan_dub.config import BodhanConfig
from bodhan_dub.core.orchestrator import BodhanDubbingOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("bodhan_dub")

def parse_args():
    parser = argparse.ArgumentParser(
        prog="bodhan_dub",
        description="Bodhan.AI Agentic Video Dubbing Pipeline (Assignment 4)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to input video (MP4, MOV, MKV, WebM; 1-15 min)."
    )
    parser.add_argument(
        "--langs",
        type=str,
        required=True,
        help="Comma-separated list of target language codes (e.g. hi,ta,bn)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("./out"),
        help="Root output directory."
    )
    parser.add_argument(
        "--glossary",
        type=Path,
        default=None,
        help="Optional YAML file specifying glossary terms."
    )
    parser.add_argument(
        "--voices",
        type=Path,
        default=None,
        help="Optional YAML file specifying voice mappings per language/speaker."
    )
    parser.add_argument(
        "--code-mixed",
        action="store_true",
        help="Enable code-mixed translation support (preserve technical loanwords)."
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in mock/offline mode (uses compliant synthetic models without live API calls)."
    )
    return parser.parse_args()

def main():
    args = parse_args()
    print("=" * 70)
    print("      BODHAN.AI AGENTIC VIDEO DUBBING PIPELINE")
    print("=" * 70)

    if not args.input.exists():
        logger.error("Input file does not exist: %s", args.input)
        sys.exit(1)

    # Parse and validate target languages
    target_langs = [lang.strip().lower() for lang in args.langs.split(",") if lang.strip()]
    if not target_langs:
        logger.error("No valid target languages specified in --langs.")
        sys.exit(1)

    print(f"[*] Input Video    : {args.input.resolve()}")
    print(f"[*] Target Langs   : {', '.join(target_langs)}")
    print(f"[*] Output Dir     : {args.out.resolve()}")
    print(f"[*] Code-Mixed     : {args.code_mixed}")
    print(f"[*] Execution Mode : {'MOCK / HERMETIC TEST' if args.mock else 'LIVE BODHAN HOSTED APIS'}")

    # Load optional glossary
    glossary_data = {}
    if args.glossary and args.glossary.exists():
        try:
            with open(args.glossary, encoding="utf-8") as f:
                glossary_data = yaml.safe_load(f) or {}
            print(f"[*] Loaded Glossary: {args.glossary.name} ({len(glossary_data)} terms)")
        except Exception as e:
            logger.warning("Could not read glossary file: %s", e)

    # Load optional voices
    voices_data = {}
    if args.voices and args.voices.exists():
        try:
            with open(args.voices, encoding="utf-8") as f:
                voices_data = yaml.safe_load(f) or {}
            print(f"[*] Loaded Voices  : {args.voices.name} ({len(voices_data)} mappings)")
        except Exception as e:
            logger.warning("Could not read voices file: %s", e)

    # Validate API keys if running live
    if not args.mock:
        if not BodhanConfig.TRANSCRIBE_API_KEY:
            logger.warning("BODHAN_API_KEY (or BODHAN_TRANSCRIBE_API_KEY) is not set. Live transcription will fail.")
        if not BodhanConfig.TRANSLATE_API_KEY:
            logger.warning("BODHAN_API_KEY (or BODHAN_TRANSLATE_API_KEY) is not set. Live translation will fail.")
        if not BodhanConfig.SPEAK_API_KEY:
            logger.warning("BODHAN_API_KEY (or BODHAN_SPEAK_API_KEY) is not set. Live TTS will fail.")

    orchestrator = BodhanDubbingOrchestrator(
        output_dir=args.out,
        glossary=glossary_data,
        voices=voices_data,
        code_mixed=args.code_mixed,
        mock_mode=args.mock
    )

    try:
        report = orchestrator.run(
            input_video=args.input,
            target_languages=target_langs
        )
        print("\n" + "=" * 70)
        print("                 PIPELINE COMPLETED SUCCESSFULLY")
        print("=" * 70)
        print(f"Overall Status   : {report.overall_status}")
        print(f"Total Time       : {report.total_execution_time_seconds:.2f}s")
        print(f"Duration Drift   : {report.duration_difference_ms:.1f}ms (Within spec: {report.duration_within_spec})")
        print(f"Summary Report   : {args.out / 'report.json'}")
        print(f"English Transcript: {args.out / 'transcript.en.json'}")
        for lang_code, s in report.languages.items():
            print(f" - [{lang_code.upper()}] Video: {s.output_video_path} | Subtitles: {s.subtitles_srt_path} | CER: {s.average_cer}")
        print("=" * 70)
        sys.exit(0)
    except Exception as e:
        logger.exception("Pipeline execution failed: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
