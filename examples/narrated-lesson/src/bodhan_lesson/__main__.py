"""CLI for the whole pipeline: PDF -> Indic-OCR -> cleanup -> lesson.src.md -> English narration script
-> translate -> speak -> back-transcribe (CER) -> MP3 and report.json.

    python -m bodhan_lesson --input chapter.pdf --out ./out --langs hi ta bn
    python -m bodhan_lesson --from-script --out ./out --langs ta     # only translate and speak
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from bodhan_lesson import config, storage
from bodhan_lesson.clients.bodhan import BodhanClient
from bodhan_lesson.clients.llm import LlmClient
from bodhan_lesson.stages import ingest, ocr
from bodhan_lesson.stages.assemble import assemble
from bodhan_lesson.stages.cleanup import cleanup_pages
from bodhan_lesson.stages.figures import describe_figures
from bodhan_lesson.stages.localize import localize
from bodhan_lesson.stages.narrate import narrate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bodhan_lesson", description=__doc__)
    parser.add_argument("--input", type=Path, help="PDF or folder of page images (not needed with --from-script)")
    parser.add_argument("--out", type=Path, default=Path("out"), help="output directory")
    parser.add_argument("--pages", type=int, default=None, help="only process the first N pages")
    parser.add_argument("--dpi", type=int, default=config.OCR_DPI)
    parser.add_argument("--env", type=Path, default=Path(".env"), help="path to the .env file")
    parser.add_argument("--no-llm", action="store_true", help="skip LLM stages; narration uses rule-based speech")
    parser.add_argument("--cache-dir", type=Path, default=Path(".bodhan_cache"), help="LLM response cache")
    parser.add_argument("--langs", nargs="+", default=[], metavar="CODE",
                        help="target languages to translate and speak, e.g. hi ta bn (any of Indic-Speak's 13)")
    parser.add_argument("--no-audio", action="store_true", help="translate only; skip speech synthesis")
    parser.add_argument("--from-script", action="store_true",
                        help="reuse out/source/narration_script.en.json: only translate and speak")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    unknown = [code for code in args.langs if code not in config.SPEAK_LANGUAGES]
    if unknown:
        print(f"error: unsupported language(s) {unknown}; choose from {', '.join(config.SPEAK_LANGUAGES)}", file=sys.stderr)
        return 2
    if args.from_script:
        return _localize(args)
    if args.input is None:
        print("error: --input is required unless --from-script is given", file=sys.stderr)
        return 2
    try:
        settings = config.load_settings(args.env)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    source_dir = args.out / "source"
    images = ingest.ingest(args.input, args.out / "pages_png", dpi=args.dpi, max_pages=args.pages)
    pages = ocr.run_ocr(ocr.IndicOcrClient(settings.ocr_key), images, source_dir / "pages")

    summary = cleanup_pages(pages, figure_dir=source_dir / "figures")
    storage.save_pages(pages, source_dir / "pages")
    storage.write_json(source_dir / "cleanup_summary.json", summary)

    lesson = assemble(pages)
    storage.save_lesson(lesson, source_dir)
    summary["sections"] = len(lesson.sections)
    summary["elements"] = len(lesson.elements)
    summary["table_joins"] = lesson.table_joins

    llm_settings = None if args.no_llm else config.load_llm_settings(args.env)
    if llm_settings is None:
        logging.warning("LLM not configured (or --no-llm): figures use captions and narration is rule-based")
    client = LlmClient(llm_settings, cache_dir=args.cache_dir) if llm_settings else None
    figures = describe_figures(lesson, source_dir, client)
    storage.save_figures(figures, source_dir)
    sections, stats = narrate(lesson, figures, client)
    storage.save_narration(sections, stats, source_dir)
    summary["figures_described"] = sum(f.source == "vision" for f in figures.values())
    summary["narration"] = {"units": sum(len(s.units) for s in sections), **vars(stats)}
    print(json.dumps(summary, indent=2))
    return _localize(args) if args.langs else 0


def _localize(args: argparse.Namespace) -> int:
    """Stages 6-7: translate the narration script and speak it, per language."""
    try:
        keys = config.load_bodhan_keys(args.env)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    client = BodhanClient(keys, cache_dir=args.cache_dir)
    report = localize(args.out, args.langs, client, speak=not args.no_audio)
    print(json.dumps({lang: {k: v for k, v in r.items() if k not in ("sections",)}
                      for lang, r in report["languages"].items()}, indent=2, ensure_ascii=False))
    failed = any("fatal_error" in r for r in report["languages"].values())
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
