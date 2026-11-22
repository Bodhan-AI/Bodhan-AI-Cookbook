"""CLI entry helpers for the narrated-lesson pipeline.

Pages → OCR → layout → assemble → translate → narrate → speak → report.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from bodhan_lesson.assemble import assemble_lesson
from bodhan_lesson.languages import (
    parse_lang_list,
    validate_source_lang,
    validate_target_langs,
)
from bodhan_lesson.layout_repair import run_layout_on_pages
from bodhan_lesson.narrate import narrate_translated_lessons
from bodhan_lesson.ocr import load_existing_ocr_pages, run_ocr_on_pages
from bodhan_lesson.render_pages import prepare_page_images
from bodhan_lesson.speak import speak_narrated_lessons
from bodhan_lesson.translate import translate_lesson_file


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
    assembly: dict | None = None,
    translations: list[dict] | None = None,
    narrations: list[dict] | None = None,
    speaks: list[dict] | None = None,
) -> Path:
    """Write report.json after OCR through speak."""
    layout_pages = layout_pages or []
    assembly = assembly or {}
    translations = translations or []
    narrations = narrations or []
    speaks = speaks or []

    ocr_ok = sum(1 for row in ocr_pages if row.get("status") == "ok")
    ocr_failed = sum(1 for row in ocr_pages if row.get("status") == "error")
    layout_ok = sum(1 for row in layout_pages if row.get("status") == "ok")
    layout_failed = sum(1 for row in layout_pages if row.get("status") == "error")
    llm_repairs = sum(1 for row in layout_pages if row.get("repair") == "llm")
    fallbacks = sum(1 for row in layout_pages if row.get("repair") == "fallback")
    skip_ocr = bool(getattr(args, "skip_ocr", False))

    translate_ok = sum(1 for row in translations if row.get("status") == "ok")
    translate_failed = sum(1 for row in translations if row.get("status") == "error")
    narrate_ok = sum(1 for row in narrations if row.get("status") == "ok")
    narrate_failed = sum(
        1 for row in narrations if row.get("status") in {"error", "skipped"}
    )
    speak_ok = sum(1 for row in speaks if row.get("status") == "ok")
    speak_failed = sum(
        1 for row in speaks if row.get("status") in {"error", "skipped", "partial"}
    )

    narrate_llm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    narrate_translate_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    for row in narrations:
        usage = row.get("llm_usage") or {}
        for key in narrate_llm_usage:
            narrate_llm_usage[key] += int(usage.get(key) or 0)
        usage = row.get("translate_usage") or {}
        for key in narrate_translate_usage:
            narrate_translate_usage[key] += int(usage.get(key) or 0)

    assemble_status = assembly.get("status")
    if ocr_failed and not ocr_ok:
        status, message, phase = "ocr_failed", "All pages failed OCR", 2
    elif layout_failed and not layout_ok and ocr_ok:
        status, message, phase = (
            "layout_failed",
            "OCR ok but all layout cleanup failed",
            2,
        )
    elif assemble_status == "error":
        status, message, phase = (
            "assemble_failed",
            "Layout ok but assemble produced no lesson",
            3,
        )
    elif translations and translate_failed and not translate_ok:
        status, message, phase = (
            "translate_failed",
            "All target languages failed translation",
            4,
        )
    elif narrations and narrate_failed and not narrate_ok:
        status, message, phase = (
            "narrate_failed",
            "Translations ok but all narration scripts failed",
            5,
        )
    elif speaks and speak_failed and not speak_ok:
        status, message, phase = (
            "speak_failed",
            "Scripts ok but all speech synthesis failed",
            6,
        )
    elif (
        ocr_failed
        or layout_failed
        or assemble_status == "partial"
        or (translations and translate_failed)
        or (narrations and narrate_failed)
        or (speaks and speak_failed)
    ):
        if speaks:
            status, message, phase = (
                "speak_partial",
                "Completed with gaps — see report stages",
                6,
            )
        elif narrations:
            status, message, phase = (
                "narrate_partial",
                "Completed with gaps — see report stages",
                5,
            )
        elif translations:
            status, message, phase = (
                "translate_partial",
                "Completed with gaps — see report stages",
                4,
            )
        else:
            status, message, phase = (
                "assemble_partial",
                "Completed with gaps — see report stages",
                3,
            )
    elif speaks and speak_ok == len(speaks):
        status = "speak_ready"
        message = (
            "Reused OCR; layout through speak complete"
            if skip_ocr
            else "OCR through speak complete"
        )
        phase = 6
    elif narrations and narrate_ok == len(narrations):
        status, message, phase = "narrate_ready", "Narrate complete", 5
    elif translations and translate_ok == len(translations):
        status, message, phase = "translate_ready", "Translate complete", 4
    elif assemble_status == "ok":
        status, message, phase = "assemble_ready", "Assemble complete", 3
    else:
        status, message, phase = "layout_ready", "OCR and layout complete", 2

    lang_status: dict[str, dict] = {}
    by_lang = {row.get("lang"): row for row in translations}
    by_narrate = {row.get("lang"): row for row in narrations}
    by_speak = {row.get("lang"): row for row in speaks}
    for lang in langs:
        row = by_lang.get(lang)
        narr = by_narrate.get(lang)
        spk = by_speak.get(lang)
        if spk and spk.get("status") == "ok":
            lang_status[lang] = {
                "status": "spoken",
                "timings": {
                    "translate_s": (row or {}).get("latency_s"),
                    "narrate_s": (narr or {}).get("latency_s"),
                    "speak_s": spk.get("latency_s"),
                },
                "retries": spk.get("retries") or 0,
                "qa": {},
                "cost": {
                    "translate_usage": (row or {}).get("usage") or {},
                    "narrate_llm_usage": (narr or {}).get("llm_usage") or {},
                    "narrate_translate_usage": (narr or {}).get("translate_usage") or {},
                },
                "structure_ok": (row or {}).get("structure_ok"),
                "structure_issues": (row or {}).get("structure_issues") or [],
                "path": (row or {}).get("path"),
                "narration_script": (narr or {}).get("path"),
                "lesson_mp3": spk.get("lesson_mp3"),
                "voice": spk.get("voice"),
                "sections_ok": spk.get("sections_ok"),
                "sections_skipped": spk.get("sections_skipped"),
                "ai_generated": True,
            }
        elif spk and spk.get("status") in {"error", "skipped", "partial"}:
            lang_status[lang] = {
                "status": "speak_error",
                "timings": {
                    "translate_s": (row or {}).get("latency_s"),
                    "narrate_s": (narr or {}).get("latency_s"),
                    "speak_s": spk.get("latency_s"),
                },
                "retries": spk.get("retries") or 0,
                "qa": {},
                "cost": {},
                "error": spk.get("error"),
                "path": (row or {}).get("path"),
                "narration_script": (narr or {}).get("path"),
                "ai_generated": True,
            }
        elif narr and narr.get("status") == "ok":
            lang_status[lang] = {
                "status": "scripted",
                "timings": {
                    "translate_s": (row or {}).get("latency_s"),
                    "narrate_s": narr.get("latency_s"),
                },
                "retries": 0,
                "qa": {},
                "cost": {
                    "translate_usage": (row or {}).get("usage") or {},
                    "narrate_llm_usage": narr.get("llm_usage") or {},
                    "narrate_translate_usage": narr.get("translate_usage") or {},
                },
                "structure_ok": (row or {}).get("structure_ok"),
                "structure_issues": (row or {}).get("structure_issues") or [],
                "path": (row or {}).get("path"),
                "narration_script": narr.get("path"),
                "sections_ok": narr.get("ok"),
                "sections_skipped": narr.get("skipped"),
            }
        elif narr and narr.get("status") in {"error", "skipped"}:
            lang_status[lang] = {
                "status": "narrate_error",
                "timings": {
                    "translate_s": (row or {}).get("latency_s"),
                    "narrate_s": narr.get("latency_s"),
                },
                "retries": 0,
                "qa": {},
                "cost": {},
                "error": narr.get("error"),
                "path": (row or {}).get("path"),
            }
        elif row and row.get("status") == "ok":
            lang_status[lang] = {
                "status": "translated",
                "timings": {"translate_s": row.get("latency_s")},
                "retries": 0,
                "qa": {},
                "cost": {"translate_usage": row.get("usage") or {}},
                "structure_ok": row.get("structure_ok"),
                "structure_issues": row.get("structure_issues") or [],
                "path": row.get("path"),
            }
        elif row:
            lang_status[lang] = {
                "status": "translate_error",
                "timings": {"translate_s": row.get("latency_s")},
                "retries": 0,
                "qa": {},
                "cost": {},
                "error": row.get("error"),
            }
        else:
            lang_status[lang] = {
                "status": "pending",
                "timings": {},
                "retries": 0,
                "qa": {},
                "cost": {},
            }

    report = {
        "status": status,
        "phase": phase,
        "message": message,
        "input": str(Path(args.input).resolve()),
        "page_count": page_count,
        "langs": langs,
        "source_lang": args.source_lang or "en",
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
            "llm_usage": {
                "prompt_tokens": sum(
                    int((row.get("llm_usage") or {}).get("prompt_tokens") or 0)
                    for row in layout_pages
                ),
                "completion_tokens": sum(
                    int((row.get("llm_usage") or {}).get("completion_tokens") or 0)
                    for row in layout_pages
                ),
                "total_tokens": sum(
                    int((row.get("llm_usage") or {}).get("total_tokens") or 0)
                    for row in layout_pages
                ),
            },
            "pages": layout_pages,
        },
        "assembly": assembly,
        "translations": {
            "ok": translate_ok,
            "failed": translate_failed,
            "usage": {
                "prompt_tokens": sum(
                    int((row.get("usage") or {}).get("prompt_tokens") or 0)
                    for row in translations
                ),
                "completion_tokens": sum(
                    int((row.get("usage") or {}).get("completion_tokens") or 0)
                    for row in translations
                ),
                "total_tokens": sum(
                    int((row.get("usage") or {}).get("total_tokens") or 0)
                    for row in translations
                ),
            },
            "pages": translations,
        },
        "narrations": {
            "ok": narrate_ok,
            "failed": narrate_failed,
            "llm_usage": narrate_llm_usage,
            "translate_usage": narrate_translate_usage,
            "pages": narrations,
        },
        "speaks": {
            "ok": speak_ok,
            "failed": speak_failed,
            "pages": speaks,
        },
        "languages": lang_status,
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    """Run through speak and write report."""
    load_dotenv()
    args = parse_args(argv)

    input_path = Path(args.input)
    out_dir = Path(args.out)
    langs = parse_lang_list(args.langs)
    source_lang = args.source_lang or "en"

    try:
        validate_target_langs(langs)
        validate_source_lang(args.source_lang)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2

    ensure_output_dirs(out_dir, langs)
    pages_dir = out_dir / "source" / "pages"
    source_dir = out_dir / "source"

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
    assembly = assemble_lesson(pages_dir, source_dir=source_dir)

    translations: list[dict] = []
    if assembly.get("status") != "error":
        lesson_path = Path(assembly["lesson_path"])
        try:
            translations = translate_lesson_file(
                lesson_path,
                out_dir,
                langs,
                source_lang=source_lang,
            )
        except Exception as exc:  # noqa: BLE001 — still write report
            translations = [
                {
                    "lang": lang,
                    "status": "error",
                    "path": None,
                    "error": str(exc),
                    "usage": {},
                    "structure_ok": False,
                    "structure_issues": [],
                    "latency_s": 0.0,
                    "pivot": False,
                    "chunks": 0,
                }
                for lang in langs
            ]

    narrations: list[dict] = []
    if translations:
        narrations = narrate_translated_lessons(out_dir, translations)

    speaks: list[dict] = []
    if narrations:
        voices_path = Path(args.voices) if args.voices else None
        speaks = speak_narrated_lessons(
            out_dir,
            narrations,
            voices_path=voices_path,
        )

    report_path = write_report(
        out_dir,
        args,
        langs,
        page_count=page_count,
        ocr_pages=ocr_pages,
        layout_pages=layout_pages,
        assembly=assembly,
        translations=translations,
        narrations=narrations,
        speaks=speaks,
    )

    ocr_ok = sum(1 for row in ocr_pages if row.get("status") == "ok")
    ocr_failed = len(ocr_pages) - ocr_ok
    layout_ok = sum(1 for row in layout_pages if row.get("status") == "ok")
    layout_failed = len(layout_pages) - layout_ok
    translate_ok = sum(1 for row in translations if row.get("status") == "ok")
    translate_failed = len(translations) - translate_ok
    narrate_ok = sum(1 for row in narrations if row.get("status") == "ok")
    narrate_failed = len(narrations) - narrate_ok
    speak_ok = sum(1 for row in speaks if row.get("status") == "ok")
    speak_failed = len(speaks) - speak_ok

    if (
        assembly.get("status") == "ok"
        and translate_ok
        and not translate_failed
        and narrate_ok
        and not narrate_failed
        and speak_ok
        and not speak_failed
        and ocr_ok
        and not ocr_failed
        and not layout_failed
    ):
        print("Speak OK")
    elif speak_ok or narrate_ok or translate_ok or assembly.get("ok"):
        print("Speak partial")
    else:
        print("Speak failed")
    print(f"  input : {input_path}")
    print(f"  pages : {page_count} -> {pages_dir}")
    if args.skip_ocr:
        print("  ocr   : reused existing .ocr.json (--skip-ocr)")
    else:
        print(f"  ocr   : {ocr_ok} ok, {ocr_failed} failed")
    print(f"  layout: {layout_ok} ok, {layout_failed} failed")
    print(f"  assemble: {assembly.get('status')} -> {assembly.get('lesson_path')}")
    print(f"  translate: {translate_ok} ok, {translate_failed} failed")
    print(f"  narrate: {narrate_ok} ok, {narrate_failed} failed")
    print(f"  speak : {speak_ok} ok, {speak_failed} failed")
    print(f"  langs : {langs} (source={source_lang})")
    print(f"  out   : {out_dir.resolve()}")
    print(f"  report: {report_path}")

    if ocr_ok == 0 or assembly.get("status") == "error":
        return 2
    if translations and translate_ok == 0:
        return 2
    if narrations and narrate_ok == 0:
        return 2
    if speaks and speak_ok == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
