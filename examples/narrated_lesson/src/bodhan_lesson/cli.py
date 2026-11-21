"""CLI entry helpers for the narrated-lesson pipeline.

Parses command-line flags, prepares page images, and writes a stub report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bodhan_lesson.render_pages import prepare_page_images


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read CLI flags into a simple args object."""
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
    """Create the output layout the assignment expects."""
    (out_dir / "source" / "pages").mkdir(parents=True, exist_ok=True)
    for lang in langs:
        lang_dir = out_dir / lang
        (lang_dir / "narration").mkdir(parents=True, exist_ok=True)


def write_stub_report(
    out_dir: Path,
    args: argparse.Namespace,
    langs: list[str],
    page_count: int,
) -> Path:
    """Write a starter report.json (filled more in later phases)."""
    report = {
        "status": "pages_ready",
        "phase": 1,
        "message": "Page images prepared — OCR/translate/narration not implemented yet",
        "input": str(Path(args.input).resolve()),
        "page_count": page_count,
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
    """Run Phase 0+1: parse flags, prepare page images, write stub report."""
    args = parse_args(argv)

    input_path = Path(args.input)
    out_dir = Path(args.out)
    langs = [part.strip() for part in args.langs.split(",") if part.strip()]

    if not langs:
        print("error: --langs must list at least one language code, e.g. hi,ta,bn")
        return 2

    ensure_output_dirs(out_dir, langs)
    pages_dir = out_dir / "source" / "pages"

    try:
        page_paths = prepare_page_images(input_path, pages_dir)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 2

    report_path = write_stub_report(out_dir, args, langs, page_count=len(page_paths))

    print("Phase 1 OK")
    print(f"  input : {input_path}")
    print(f"  pages : {len(page_paths)} -> {pages_dir}")
    print(f"  langs : {langs}")
    print(f"  out   : {out_dir.resolve()}")
    print(f"  report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
