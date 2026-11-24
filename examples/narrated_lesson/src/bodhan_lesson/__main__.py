"""Command line entry point: python -m bodhan_lesson --input scans/ --langs hi,ta,bn --out ./out"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .client import BodhanClient
from .pipeline import run_pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bodhan_lesson",
        description="Turn scanned textbook pages into a translated lesson (narration audio is not implemented yet).")
    ap.add_argument("--input", required=True, help="a PDF or a folder of PNG/JPG page images")
    ap.add_argument("--langs", required=True, help="target languages, comma separated, for example hi,ta,bn")
    ap.add_argument("--out", default="out", help="output folder (default: out)")
    ap.add_argument("--max-pages", type=int, default=None, help="only process the first N pages")
    ap.add_argument("--workers", type=int, default=4, help="parallel API calls during translation")
    args = ap.parse_args(argv)

    langs = [c.strip() for c in args.langs.split(",") if c.strip()]
    if not langs:
        ap.error("--langs needs at least one language code")
    try:
        client = BodhanClient()
        report = run_pipeline(client, Path(args.input), langs, Path(args.out), args.max_pages, args.workers)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ocr = report["stages"]["ocr"]
    print(f"OCR: {ocr['pages']} pages, {len(ocr['failed_pages'])} failed, {ocr['seconds']}s")
    for lang, rep in report["stages"]["translation"].items():
        if "error" in rep:
            print(f"{lang}: FAILED {rep['error']}")
        else:
            print(f"{lang}: {rep['elements']} elements, {len(rep['translation_issues'])} issues, "
                  f"structure ok={rep['structure']['same_order_and_types']}, {rep['seconds']}s, Rs {rep['cost_inr']}")
    print(f"Total cost: Rs {report['cost']['total_inr']}, time {report['total_seconds']}s")
    print("Not implemented yet:", ", ".join(report["not_implemented"]))
    print(f"Report: {Path(args.out) / 'report.json'}")
    for p in report["problems"]:
        print("PROBLEM:", p)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())