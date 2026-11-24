"""Command-line entry point for the scanned-chapter lesson pipeline."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from backend.config import LANGUAGE_NAMES, LessonRequest, read_environment
from backend.models import AudioRun, NarrationPlan, NarrationScripts, SpeechQARun, SpeechRun, TranslationQARun, TranslationRun
from backend.report import audio_run_status
from backend.runner import CHECKPOINTS, run_lesson


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _confirm_resume(out: Path) -> bool:
    while True:
        print(f"Saved work matches this input in {out}. Resume [Y/n]? ", end="", file=sys.stderr, flush=True)
        try:
            answer = input().strip().casefold()
        except EOFError:
            _progress("")
            return True
        if answer in {"", "y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        _progress("Enter y to resume or n to clear generated output and start over.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="PNG/JPEG folder or scanned PDF")
    parser.add_argument("--langs", required=True, help="Comma-separated target language codes")
    parser.add_argument("--out", type=Path, required=True, help="Output directory")
    parser.add_argument("--source-lang", help="Source language override")
    parser.add_argument("--until", choices=CHECKPOINTS, help="Stop after this checkpoint")
    parser.add_argument("--force", action="store_true", help="Clear generated output and start over")
    parser.add_argument("--glossary", type=Path, help="Optional glossary for translation")
    parser.add_argument("--voices", type=Path, help="Optional voice overrides for narration")
    parser.add_argument("--code-mixed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    targets = tuple(code.strip().lower() for code in args.langs.split(","))
    invalid = [code for code in targets if code not in LANGUAGE_NAMES]
    if invalid:
        parser.error(f"Unsupported --langs code(s): {', '.join(repr(code) for code in invalid)}")
    if args.code_mixed:
        parser.error("--code-mixed narration is not available.")
    request = LessonRequest(args.input, targets, args.out, args.source_lang, args.until,
                            args.glossary, args.voices, force=args.force)
    try:
        result = run_lesson(request, read_environment(os.environ), progress=_progress,
                            confirm_resume=_confirm_resume if sys.stdin.isatty() else None)
    except KeyboardInterrupt:
        _progress("\nInterrupted. Rerun the same command to resume saved work.")
        return 130
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if isinstance(result, SpeechRun) and result.failed_languages:
        print(f"Speech synthesis failed for {', '.join(result.failed_languages)}. "
              "See each target's chunks.json for details.", file=sys.stderr)
        return 1
    if isinstance(result, SpeechQARun) and result.failed_languages:
        print(f"Speech QA failed for {', '.join(result.failed_languages)}. "
              "See each target's speech-qa.json for details.", file=sys.stderr)
        return 1
    if isinstance(result, AudioRun) and audio_run_status(result) in {"partial", "failed"}:
        for target in result.targets:
            if target.status == "failed":
                _progress(f"Audio export failed for {target.language}. {target.reason or ''}")
        return 1
    if isinstance(result, (TranslationRun, TranslationQARun, NarrationPlan, NarrationScripts)) and result.failed_languages:
        artifact = "segments.json" if isinstance(result, TranslationRun) else "translation-qa.json"
        print(f"Translation failed for {', '.join(result.failed_languages)}. "
              f"See each target's {artifact} for details.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
