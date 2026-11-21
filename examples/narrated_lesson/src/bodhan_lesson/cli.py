"""
CLI entry helpers for the narrated-lesson pipeline.
Parses command-line flags and prepares the output folder layout.

"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bodhan_lesson",
        description="Scanned chapter → translated, narrated lesson (Bodhan Assignment 2).",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Folder of page images (PNG/JPG) or a single scanned PDF",
    )
    parser.add_argument(
        "--langs",
        required=True,
        help="Comma-separated target languages, e.g. hi,ta,bn",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory",
    )
    parser.add_argument("--source-lang", default=None, help="Optional source language (e.g. en)")
    parser.add_argument("--glossary", default=None, help="Optional glossary YAML path")
    parser.add_argument("--voices", default=None, help="Optional voices YAML path")
    parser.add_argument(
        "--code-mixed",
        action="store_true",
        help="Optional code-mixed narration mode",
    )
    return parser.parse_args(argv)


def ensure_output_dirs(out_dir: Path, langs: list[str]) -> None:
    """Create the output layout the assignment expects (empty shells for now)."""
    (out_dir / "source" / "pages").mkdir(parents=True, exist_ok=True)
    for lang in langs:
        lang_dir = out_dir / lang
        (lang_dir / "narration").mkdir(parents=True, exist_ok=True)


def write_stub_report(out_dir: Path, args: argparse.Namespace, langs: list[str]) -> Path:
    report = {
        "status": "stub",
        "phase": 0,
        "message": "CLI skeleton only — pipeline not implemented yet",
        "input": str(Path(args.input).resolve()),
        "langs": langs,
        "source_lang": args.source_lang,
        "code_mixed": bool(args.code_mixed),
        "glossary": args.glossary,
        "voices": args.voices,
        "languages": {
            lang: {"status": "pending", "timings": {}, "retries": 0, "qa": {}, "cost": {}}
            for lang in langs
        },
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    input_path = Path(args.input)
    out_dir = Path(args.out)
    langs = [part.strip() for part in args.langs.split(",") if part.strip()]

    if not langs:
        print("error: --langs must list at least one language code, e.g. hi,ta,bn")
        return 2

    if not input_path.exists():
        # Phase 0: warn but still create stub dirs so you can test the CLI wiring
        print(f"warning: input path does not exist yet: {input_path}")

    ensure_output_dirs(out_dir, langs)
    report_path = write_stub_report(out_dir, args, langs)

    print(f"Phase 0 OK")
    print(f"  input : {input_path}")
    print(f"  langs : {langs}")
    print(f"  out   : {out_dir.resolve()}")
    print(f"  report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())