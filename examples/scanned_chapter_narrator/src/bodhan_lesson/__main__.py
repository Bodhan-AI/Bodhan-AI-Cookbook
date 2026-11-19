"""CLI entrypoint for Bodhan Lesson: Scanned Chapter to Multilingual Narrated Lesson."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from bodhan_lesson.config import SUPPORTED_LANGUAGES
from bodhan_lesson.pipeline import pipeline


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bodhan_lesson",
        description="Transform scanned textbook chapters into multilingual narrated lessons with Bodhan Indic AI.",
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Path to input scanned PDF or folder of page images.",
    )
    parser.add_argument(
        "--langs",
        "-l",
        default="hi,ta,bn",
        help="Comma-separated target Indic language codes (e.g. 'hi,ta,bn', default: 'hi,ta,bn').",
    )
    parser.add_argument(
        "--out",
        "-o",
        default="./out",
        help="Output directory path (default: './out').",
    )
    parser.add_argument(
        "--source-lang",
        "--src-lang",
        dest="src_lang",
        default="auto",
        help="Source document language (default: 'auto' with Indic detection).",
    )
    parser.add_argument(
        "--glossary",
        default=None,
        help="Optional custom glossary YAML/JSON file for technical terminology.",
    )
    parser.add_argument(
        "--voices",
        default=None,
        help="Optional custom voices YAML/JSON file mapping languages to voices.",
    )
    parser.add_argument(
        "--code-mixed",
        action="store_true",
        help="Enable code-mixed narration preserving English technical terms.",
    )
    parser.add_argument(
        "--skip-qa",
        action="store_true",
        help="Skip ASR back-transcription QA checks to accelerate processing.",
    )
    parser.add_argument(
        "--fast-narration",
        "--deterministic-narration",
        dest="fast_narration",
        action="store_true",
        help="Use deterministic rule-based narration verbalizer (0 LLM tokens, 0.01s per section).",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="Optional voice override for speech synthesis.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force mock mode for local offline execution.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed debug logging.",
    )

    args = parser.parse_args()

    # Logging setup
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.mock:
        os.environ["BODHAN_MOCK"] = "1"
        import bodhan_lesson.config
        bodhan_lesson.config.MOCK_MODE = True

    raw_langs = [code.strip().lower() for code in args.langs.split(",") if code.strip()]
    if not raw_langs:
        print("Error: No target languages specified in --langs.", file=sys.stderr)
        return 1

    valid_langs = [c for c in raw_langs if c in SUPPORTED_LANGUAGES]
    unsupported_langs = [c for c in raw_langs if c not in SUPPORTED_LANGUAGES]

    if unsupported_langs:
        logging.warning(
            f"Unsupported language code(s) detected: {unsupported_langs}. "
            f"Authorized Bodhan Indic languages: {sorted(SUPPORTED_LANGUAGES)}"
        )

    if not valid_langs:
        print(
            f"Error: None of the requested languages {raw_langs} are supported by Bodhan models.\n"
            f"Supported Indic language codes are: {sorted(SUPPORTED_LANGUAGES)}",
            file=sys.stderr,
        )
        return 1

    langs = valid_langs

    custom_glossary: dict[str, str] | None = None
    if args.glossary and Path(args.glossary).is_file():
        try:
            import json

            custom_glossary = json.loads(Path(args.glossary).read_text(encoding="utf-8"))
        except Exception:
            custom_glossary = {}
            for line in Path(args.glossary).read_text(encoding="utf-8").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    custom_glossary[k.strip()] = v.strip().strip('"\'')

    custom_voices: dict[str, str] | None = None
    if args.voices and Path(args.voices).is_file():
        try:
            import json

            custom_voices = json.loads(Path(args.voices).read_text(encoding="utf-8"))
        except Exception:
            custom_voices = {}
            for line in Path(args.voices).read_text(encoding="utf-8").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    custom_voices[k.strip()] = v.strip().strip('"\'')

    try:
        report = pipeline.run(
            input_path=args.input,
            target_languages=langs,
            output_dir=args.out,
            source_language=args.src_lang,
            skip_qa=args.skip_qa,
            voice_override=args.voice,
            code_mixed=args.code_mixed,
            custom_glossary=custom_glossary,
            custom_voices=custom_voices,
            fast_narration=args.fast_narration,
        )

        print("\n" + "=" * 60)
        print("BODHAN LESSON PIPELINE EXECUTION SUMMARY")
        print("=" * 60)
        print(f"Status           : {report['status']}")
        print(f"Total Pages      : {report['source_document']['total_pages']}")
        print(f"Sections         : {report['source_document']['total_sections']}")
        print(f"Equations Found  : {report['source_document']['total_equations']}")
        print(f"Target Languages : {', '.join(report['target_languages'])}")
        print(f"Execution Time   : {report['execution_time_seconds']}s")
        print(f"Total Cost       : ₹{report['financial_audit']['total_cost_inr']} INR")
        if report.get("errors"):
            print("Errors           :")
            for err in report["errors"]:
                print(f"  - {err}")
        print(f"Full Report      : {Path(args.out).resolve() / 'report.json'}")
        print("=" * 60 + "\n")

        # Exit non-zero if any language failed, as demanded by assignment line 134
        if report.get("status") != "completed":
            return 2
        return 0

    except Exception as e:
        logging.exception(f"Pipeline failed: {e}")
        print("\n" + "=" * 60)
        print("BODHAN LESSON PIPELINE EXECUTION SUMMARY")
        print("=" * 60)
        print("Status           : failed")
        print(f"Target Languages : {', '.join(raw_langs)}")
        print(f"Error            : {e}")
        print(f"Output Directory : {Path(args.out).resolve()}")
        print("=" * 60 + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
