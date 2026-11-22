"""CLI entry helpers for the narrated-lesson pipeline.

Parses flags, prepares page images, runs Indic-OCR, and writes report.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from bodhan_lesson.languages import (
    parse_lang_list,
    validate_source_lang,
    validate_target_langs,
)
from bodhan_lesson.ocr import run_ocr_on_pages
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
        help="Comma-separated target languages (Indic-Speak codes), e.g. hi,ta,bn",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory",
    )
    parser.add_argument(
        "--source-lang",
        default=None,
        help="Optional source language (translate codes, e.g. en). Detect later if omitted.",
    )
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


def write_report(
    out_dir: Path,
    args: argparse.Namespace,
    langs: list[str],
    page_count: int,
    ocr_pages: list[dict],
) -> Path:
    """Write report.json after page prep + OCR."""
    ok = sum(1 for row in ocr_pages if row.get("status") == "ok")
    failed = sum(1 for row in ocr_pages if row.get("status") == "error")
    if failed and ok:
        status = "ocr_partial"
        message = "Some pages failed OCR — see ocr.pages"
    elif failed and not ok:
        status = "ocr_failed"
        message = "All pages failed OCR"
    else:
        status = "ocr_ready"
        message = "Page images prepared and OCR complete — assemble/translate not implemented yet"

    report = {
        "status": status,
        "phase": 2,
        "message": message,
        "input": str(Path(args.input).resolve()),
        "page_count": page_count,
        "langs": langs,
        "source_lang": args.source_lang,
        "code_mixed": bool(args.code_mixed),
        "glossary": args.glossary,
        "voices": args.voices,
        "ocr": {
            "ok": ok,
            "failed": failed,
            "pages": ocr_pages,
        },
        "languages": {
            lang: {"status": "pending", "timings": {}, "retries": 0, "qa": {}, "cost": {}}
            for lang in langs
        },
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    """Run Phase 1+2: pages, Indic-OCR, report."""
    load_dotenv()
    args = parse_args(argv)

    input_path = Path(args.input)
    out_dir = Path(args.out)
    langs = parse_lang_list(args.langs)

    try:
        validate_target_langs(langs)
        validate_source_lang(args.source_lang)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2

    ensure_output_dirs(out_dir, langs)
    pages_dir = out_dir / "source" / "pages"

    try:
        page_paths = prepare_page_images(input_path, pages_dir)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 2

    try:
        ocr_pages = run_ocr_on_pages(pages_dir)
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 2

    report_path = write_report(
        out_dir,
        args,
        langs,
        page_count=len(page_paths),
        ocr_pages=ocr_pages,
    )

    ok = sum(1 for row in ocr_pages if row.get("status") == "ok")
    failed = len(ocr_pages) - ok

    print("Phase 2 OK" if ok and not failed else "Phase 2 partial" if ok else "Phase 2 failed")
    print(f"  input : {input_path}")
    print(f"  pages : {len(page_paths)} -> {pages_dir}")
    print(f"  ocr   : {ok} ok, {failed} failed")
    print(f"  langs : {langs}")
    print(f"  out   : {out_dir.resolve()}")
    print(f"  report: {report_path}")

    if ok == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
