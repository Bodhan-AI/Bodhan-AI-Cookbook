"""CLI entry helpers for the narrated-lesson pipeline.

Parses flags, prepares page images, runs Indic-OCR, cleans layout, writes report.json.
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
from bodhan_lesson.layout_repair import run_layout_on_pages
from bodhan_lesson.ocr import load_existing_ocr_pages, run_ocr_on_pages
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
    parser.add_argument(
        "--skip-ocr",
        action="store_true",
        help=(
            "Dev convenience: reuse existing page_*.ocr.json under --out/source/pages "
            "(skip PDF/image render and Indic-OCR)."
        ),
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
    layout_pages: list[dict] | None = None,
) -> Path:
    """Write report.json after page prep, OCR, and layout cleanup."""
    layout_pages = layout_pages or []
    ocr_ok = sum(1 for row in ocr_pages if row.get("status") == "ok")
    ocr_failed = sum(1 for row in ocr_pages if row.get("status") == "error")
    layout_ok = sum(1 for row in layout_pages if row.get("status") == "ok")
    layout_failed = sum(1 for row in layout_pages if row.get("status") == "error")
    llm_repairs = sum(1 for row in layout_pages if row.get("repair") == "llm")
    fallbacks = sum(1 for row in layout_pages if row.get("repair") == "fallback")
    skip_ocr = bool(getattr(args, "skip_ocr", False))
    llm_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    for row in layout_pages:
        usage = row.get("llm_usage") or {}
        for key in llm_usage:
            llm_usage[key] += int(usage.get(key) or 0)

    if ocr_failed and not ocr_ok:
        status = "ocr_failed"
        message = "All pages failed OCR"
    elif layout_failed and not layout_ok and ocr_ok:
        status = "layout_failed"
        message = "OCR ok but all layout cleanup failed"
    elif ocr_failed or layout_failed:
        status = "layout_partial"
        message = "Some pages failed OCR or layout — see ocr.pages / layout.pages"
    else:
        status = "layout_ready"
        message = (
            "Reused OCR; layout cleanup complete"
            if skip_ocr
            else "OCR and layout cleanup complete"
        )

    report = {
        "status": status,
        "phase": "2b",
        "message": message,
        "input": str(Path(args.input).resolve()),
        "page_count": page_count,
        "langs": langs,
        "source_lang": args.source_lang,
        "code_mixed": bool(args.code_mixed),
        "glossary": args.glossary,
        "voices": args.voices,
        "skip_ocr": skip_ocr,
        "ocr": {
            "ok": ocr_ok,
            "failed": ocr_failed,
            "reused": skip_ocr,
            "pages": ocr_pages,
        },
        "layout": {
            "ok": layout_ok,
            "failed": layout_failed,
            "llm_repairs": llm_repairs,
            "fallbacks": fallbacks,
            "llm_usage": llm_usage,
            "pages": layout_pages,
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
    """Prepare pages, run Indic-OCR and layout cleanup, write report."""
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

    if args.skip_ocr:
        try:
            ocr_pages = load_existing_ocr_pages(pages_dir)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2
        page_count = len(ocr_pages)
    else:
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
        page_count = len(page_paths)

    layout_pages = run_layout_on_pages(pages_dir, ocr_pages)

    report_path = write_report(
        out_dir,
        args,
        langs,
        page_count=page_count,
        ocr_pages=ocr_pages,
        layout_pages=layout_pages,
    )

    ocr_ok = sum(1 for row in ocr_pages if row.get("status") == "ok")
    ocr_failed = len(ocr_pages) - ocr_ok
    layout_ok = sum(1 for row in layout_pages if row.get("status") == "ok")
    layout_failed = len(layout_pages) - layout_ok

    if ocr_ok and layout_ok and not ocr_failed and not layout_failed:
        print("Layout OK")
    elif ocr_ok or layout_ok:
        print("Layout partial")
    else:
        print("Layout failed")
    print(f"  input : {input_path}")
    print(f"  pages : {page_count} -> {pages_dir}")
    if args.skip_ocr:
        print("  ocr   : reused existing .ocr.json (--skip-ocr)")
    else:
        print(f"  ocr   : {ocr_ok} ok, {ocr_failed} failed")
    print(f"  layout: {layout_ok} ok, {layout_failed} failed")
    print(f"  langs : {langs}")
    print(f"  out   : {out_dir.resolve()}")
    print(f"  report: {report_path}")

    if ocr_ok == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
