"""CLI entry helpers for the narrated-lesson pipeline.

Pages → OCR → layout → assemble → translate → narrate → speak → QA → reader → report.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from bodhan_lesson.assemble import assemble_lesson
from bodhan_lesson.cost import build_cost_report
from bodhan_lesson.detect_lang import detect_source_lang_from_path
from bodhan_lesson.languages import (
    parse_lang_list,
    validate_source_lang,
    validate_target_langs,
)
from bodhan_lesson.layout_repair import run_layout_on_pages
from bodhan_lesson.narrate import narrate_translated_lessons
from bodhan_lesson.ocr import load_existing_ocr_pages, run_ocr_on_pages
from bodhan_lesson.progress import log, stage
from bodhan_lesson.qa import qa_spoken_lessons
from bodhan_lesson.render_pages import prepare_page_images
from bodhan_lesson.resume import (
    FROM_CHOICES,
    ensure_prerequisites,
    load_assembly,
    load_layout_pages,
    load_narrations,
    load_qas,
    load_speaks,
    load_translations,
    require_page_images,
    reuse_plan,
    should_run,
    stage_index,
)
from bodhan_lesson.reader import write_readers_for_speaks
from bodhan_lesson.speak import speak_narrated_lessons
from bodhan_lesson.spoken_plan import ensure_spoken_plan, load_spoken_plan
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
        help=(
            "Language of the scanned page text (translate codes, e.g. en or hi). "
            "Must match the real OCR language, not --langs. "
            "Omit to auto-detect with fastText after assemble. "
            "Mismatch still runs but breaks pivot/QA."
        ),
    )
    parser.add_argument("--glossary", default=None, help="Optional glossary YAML path")
    parser.add_argument("--voices", default=None, help="Optional voices YAML path")
    parser.add_argument(
        "--code-mixed",
        action="store_true",
        help="Optional code-mixed narration mode",
    )
    parser.add_argument(
        "--from",
        dest="from_stage",
        default=None,
        choices=list(FROM_CHOICES),
        help=(
            "First stage to run; reuse artifacts before it under --out. "
            "Omit for a full new run. "
            f"Choices: {', '.join(FROM_CHOICES)}. "
            "Example: --from layout keeps OCR; --from speak keeps scripts."
        ),
    )
    return parser.parse_args(argv)


def ensure_output_dirs(out_dir: Path, langs: list[str]) -> None:
    """Create the output layout the assignment expects."""
    (out_dir / "source" / "pages").mkdir(parents=True, exist_ok=True)
    for lang in langs:
        lang_dir = out_dir / lang
        (lang_dir / "narration").mkdir(parents=True, exist_ok=True)


def _count_status(rows: list[dict], status: str = "ok") -> int:
    return sum(1 for row in rows if row.get("status") == status)


def _count_status_in(rows: list[dict], statuses: set[str] | frozenset[str]) -> int:
    return sum(1 for row in rows if row.get("status") in statuses)


def _sum_usage(rows: list[dict], usage_key: str = "usage") -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for row in rows:
        usage = row.get(usage_key) or {}
        for key in totals:
            totals[key] += int(usage.get(key) or 0)
    return totals


def _mean_cer(rows: list[dict]) -> float | None:
    values = [
        float(row["mean_cer"]) for row in rows if row.get("mean_cer") is not None
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


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
    qas: list[dict] | None = None,
    spoken_plan: dict | None = None,
    readers: list[dict] | None = None,
    source_lang: str | None = None,
    source_lang_detected: bool = False,
    source_lang_score: float | None = None,
) -> Path:
    """Write report.json after OCR through QA."""
    layout_pages = layout_pages or []
    assembly = assembly or {}
    translations = translations or []
    narrations = narrations or []
    speaks = speaks or []
    qas = qas or []
    spoken_plan = spoken_plan or {}
    readers = readers or []
    resolved_source = source_lang or args.source_lang or "en"

    ocr_ok = _count_status(ocr_pages)
    ocr_failed = _count_status(ocr_pages, "error")
    layout_ok = _count_status(layout_pages)
    layout_failed = _count_status(layout_pages, "error")
    llm_repairs = sum(1 for row in layout_pages if row.get("repair") == "llm")
    fallbacks = sum(1 for row in layout_pages if row.get("repair") == "fallback")
    from_stage = getattr(args, "from_stage", None)
    ocr_reused = bool(from_stage) and not should_run(from_stage, "ocr")

    translate_ok = _count_status(translations)
    translate_failed = _count_status(translations, "error")
    narrate_ok = _count_status(narrations)
    narrate_failed = _count_status_in(narrations, {"error", "skipped"})
    speak_ok = _count_status(speaks)
    speak_failed = _count_status_in(speaks, {"error", "skipped", "partial"})
    qa_ok = _count_status(qas)
    qa_failed = _count_status_in(qas, {"error", "partial", "skipped"})

    narrate_llm_usage = _sum_usage(narrations, "llm_usage")
    narrate_translate_usage = _sum_usage(narrations, "translate_usage")

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
    elif qas and qa_failed and not qa_ok:
        status, message, phase = (
            "qa_failed",
            "Speech ok but all languages failed QA",
            7,
        )
    elif (
        ocr_failed
        or layout_failed
        or assemble_status == "partial"
        or (translations and translate_failed)
        or (narrations and narrate_failed)
        or (speaks and speak_failed)
        or (qas and qa_failed)
    ):
        if qas:
            status, message, phase = (
                "qa_partial",
                "Completed with gaps — see report stages",
                7,
            )
        elif speaks:
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
    elif qas and qa_ok == len(qas):
        status = "qa_ready"
        message = (
            f"Pipeline complete (from={from_stage})"
            if from_stage
            else "OCR through QA complete"
        )
        phase = 7
    elif speaks and speak_ok == len(speaks):
        status = "speak_ready"
        message = (
            f"Speak complete (from={from_stage})"
            if from_stage
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
    by_qa = {row.get("lang"): row for row in qas}
    for lang in langs:
        row = by_lang.get(lang)
        narr = by_narrate.get(lang)
        spk = by_speak.get(lang)
        qa_row = by_qa.get(lang)
        if qa_row and qa_row.get("status") == "ok":
            lang_status[lang] = {
                "status": "qa_ok",
                "timings": {
                    "translate_s": (row or {}).get("latency_s"),
                    "narrate_s": (narr or {}).get("latency_s"),
                    "speak_s": (spk or {}).get("latency_s"),
                    "qa_s": qa_row.get("latency_s"),
                },
                "retries": int((spk or {}).get("retries") or 0)
                + int(qa_row.get("retries") or 0),
                "qa": {
                    "mean_cer": qa_row.get("mean_cer"),
                    "ok": qa_row.get("ok"),
                    "failed": qa_row.get("failed"),
                    "skipped": qa_row.get("skipped"),
                    "retries": qa_row.get("retries"),
                    "loudness": qa_row.get("loudness") or {},
                },
                "cost": {
                    "translate_usage": (row or {}).get("usage") or {},
                    "narrate_llm_usage": (narr or {}).get("llm_usage") or {},
                    "narrate_translate_usage": (narr or {}).get("translate_usage") or {},
                },
                "structure_ok": (row or {}).get("structure_ok"),
                "structure_issues": (row or {}).get("structure_issues") or [],
                "path": (row or {}).get("path"),
                "narration_script": (narr or {}).get("path"),
                "lesson_mp3": (spk or {}).get("lesson_mp3") or qa_row.get("lesson_mp3"),
                "voice": (spk or {}).get("voice"),
                "sections_ok": (spk or {}).get("sections_ok"),
                "sections_skipped": (spk or {}).get("sections_skipped"),
                "ai_generated": True,
            }
        elif qa_row and qa_row.get("status") in {"error", "partial", "skipped"}:
            lang_status[lang] = {
                "status": "qa_error",
                "timings": {
                    "translate_s": (row or {}).get("latency_s"),
                    "narrate_s": (narr or {}).get("latency_s"),
                    "speak_s": (spk or {}).get("latency_s"),
                    "qa_s": qa_row.get("latency_s"),
                },
                "retries": int((spk or {}).get("retries") or 0)
                + int(qa_row.get("retries") or 0),
                "qa": {
                    "mean_cer": qa_row.get("mean_cer"),
                    "ok": qa_row.get("ok"),
                    "failed": qa_row.get("failed"),
                    "skipped": qa_row.get("skipped"),
                    "retries": qa_row.get("retries"),
                    "loudness": qa_row.get("loudness") or {},
                },
                "cost": {},
                "error": qa_row.get("error"),
                "path": (row or {}).get("path"),
                "narration_script": (narr or {}).get("path"),
                "lesson_mp3": (spk or {}).get("lesson_mp3"),
                "ai_generated": True,
            }
        elif spk and spk.get("status") == "ok":
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
        "source_lang": resolved_source,
        "source_lang_detected": bool(source_lang_detected),
        "source_lang_score": source_lang_score,
        "code_mixed": bool(args.code_mixed),
        "glossary": args.glossary,
        "voices": args.voices,
        "from_stage": from_stage,
        "ocr": {
            "ok": ocr_ok,
            "failed": ocr_failed,
            "reused": ocr_reused,
            "pages": ocr_pages,
        },
        "layout": {
            "ok": layout_ok,
            "failed": layout_failed,
            "llm_repairs": llm_repairs,
            "fallbacks": fallbacks,
            "llm_usage": _sum_usage(layout_pages, "llm_usage"),
            "pages": layout_pages,
        },
        "assembly": assembly,
        "spoken_plan": {
            "path": spoken_plan.get("path"),
            "span_count": spoken_plan.get("span_count"),
            "ok": spoken_plan.get("ok"),
            "missing": spoken_plan.get("missing"),
            "source_lang": spoken_plan.get("source_lang"),
            "planning_lang": spoken_plan.get("planning_lang"),
            "notes": spoken_plan.get("notes") or [],
            "llm_usage": spoken_plan.get("llm_usage") or {},
            "pivot_usage": spoken_plan.get("pivot_usage") or {},
            "latency_s": spoken_plan.get("latency_s"),
        },
        "translations": {
            "ok": translate_ok,
            "failed": translate_failed,
            "usage": _sum_usage(translations),
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
        "readers": {
            "ok": _count_status(readers),
            "failed": _count_status_in(readers, {"error", "skipped", "empty"}),
            "pages": readers,
        },
        "qa": {
            "ok": qa_ok,
            "failed": qa_failed,
            "mean_cer": _mean_cer(qas),
            "pages": qas,
        },
        "languages": lang_status,
        "cost": build_cost_report(
            out_dir=out_dir,
            langs=langs,
            ocr_pages=ocr_pages,
            layout_pages=layout_pages,
            translations=translations,
            narrations=narrations,
            spoken_plan=spoken_plan,
        ),
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    """Run through QA and write report."""
    load_dotenv()
    args = parse_args(argv)

    input_path = Path(args.input)
    out_dir = Path(args.out)
    langs = parse_lang_list(args.langs)
    source_lang: str | None = args.source_lang
    source_lang_detected = False
    source_lang_score: float | None = None
    from_stage: str | None = args.from_stage

    try:
        validate_target_langs(langs)
        validate_source_lang(args.source_lang)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2

    ensure_output_dirs(out_dir, langs)
    pages_dir = out_dir / "source" / "pages"
    source_dir = out_dir / "source"

    plan = reuse_plan(from_stage)
    log(
        "stages: "
        + " ".join(f"{name}={action}" for name, action in plan.items())
    )

    if from_stage is not None:
        try:
            ensure_prerequisites(from_stage, pages_dir, out_dir, langs)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2

    # --- pages ---
    if should_run(from_stage, "pages"):
        stage("pages", str(input_path))
        try:
            page_paths = prepare_page_images(input_path, pages_dir)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}")
            return 2
        page_count = len(page_paths)
        log(f"  pages rendered: {page_count}")
    else:
        try:
            page_paths = require_page_images(pages_dir)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2
        page_count = len(page_paths)
        log(f"  pages reused: {page_count}")

    # --- ocr ---
    if should_run(from_stage, "ocr"):
        try:
            ocr_pages = run_ocr_on_pages(pages_dir)
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 2
    else:
        try:
            ocr_pages = load_existing_ocr_pages(pages_dir)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2

    # --- layout ---
    if should_run(from_stage, "layout"):
        layout_pages = run_layout_on_pages(pages_dir, ocr_pages)
    else:
        try:
            layout_pages = load_layout_pages(pages_dir, ocr_pages)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2

    # --- assemble ---
    if should_run(from_stage, "assemble"):
        assembly = assemble_lesson(pages_dir, source_dir=source_dir)
    else:
        try:
            assembly = load_assembly(source_dir)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2

    # Resolve source language after assemble (detect when flag omitted).
    if source_lang:
        pass
    else:
        lesson_for_detect = Path(
            assembly.get("lesson_path") or (source_dir / "lesson.src.md")
        )
        if not lesson_for_detect.is_file():
            print(
                "error: --source-lang omitted and no assembled lesson text "
                f"to detect ({lesson_for_detect})"
            )
            return 2
        try:
            source_lang, source_lang_score = detect_source_lang_from_path(
                lesson_for_detect
            )
        except ValueError as exc:
            print(f"error: {exc}")
            return 2
        source_lang_detected = True
        stage(
            "source_lang",
            f"detected={source_lang} score={source_lang_score:.2f}",
        )

    # --- translate ---
    translations: list[dict] = []
    if should_run(from_stage, "translate"):
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
    else:
        try:
            translations = load_translations(out_dir, langs)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2

    # --- spoken plan (once on source, before per-lang narrate) ---
    spoken_plan: dict = {}
    lesson_src = Path(assembly.get("lesson_path") or (source_dir / "lesson.src.md"))
    if should_run(from_stage, "narrate") and assembly.get("status") != "error":
        try:
            spoken_plan = ensure_spoken_plan(
                source_dir,
                lesson_src,
                source_lang=source_lang,
                rebuild=True,
            )
        except Exception as exc:  # noqa: BLE001 — narrate can still fall back
            log(f"  spoken_plan error: {exc}")
            spoken_plan = {
                "path": None,
                "span_count": 0,
                "ok": 0,
                "missing": 0,
                "notes": [f"spoken_plan_failed:{exc}"],
                "error": str(exc),
            }
    elif from_stage is not None and stage_index(from_stage) >= stage_index("speak"):
        spoken_plan = load_spoken_plan(source_dir) or {}

    # --- narrate ---
    narrations: list[dict] = []
    if should_run(from_stage, "narrate"):
        if translations:
            plan_for_narrate = spoken_plan if spoken_plan.get("spans") else None
            narrations = narrate_translated_lessons(
                out_dir,
                translations,
                spoken_plan=plan_for_narrate,
            )
    else:
        try:
            narrations = load_narrations(out_dir, langs)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2

    # --- speak ---
    speaks: list[dict] = []
    if should_run(from_stage, "speak"):
        if narrations:
            voices_path = Path(args.voices) if args.voices else None
            speaks = speak_narrated_lessons(
                out_dir,
                narrations,
                voices_path=voices_path,
            )
    else:
        try:
            speaks = load_speaks(out_dir, langs)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2

    # --- qa ---
    qas: list[dict] = []
    if should_run(from_stage, "qa"):
        if speaks:
            qas = qa_spoken_lessons(out_dir, speaks)
    else:
        try:
            qas = load_qas(out_dir, langs)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 2

    # Write report first so the HTML reader can embed cost / CER / timings.
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
        qas=qas,
        spoken_plan=spoken_plan,
        readers=[],
        source_lang=source_lang,
        source_lang_detected=source_lang_detected,
        source_lang_score=source_lang_score,
    )

    # HTML sync reader (after report.json so metrics appear on the page).
    readers: list[dict] = []
    if speaks:
        stage("reader", f"{len(speaks)} language(s)")
        readers = write_readers_for_speaks(
            out_dir,
            speaks,
            report_path=report_path,
        )
        for row in readers:
            if row.get("status") == "ok":
                log(f"  reader {row.get('lang')}: {row.get('path')} ({row.get('cue_count')} cues)")
            else:
                log(
                    f"  reader {row.get('lang')}: {row.get('status')}"
                    + (f" ({row.get('error')})" if row.get("error") else "")
                )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["readers"] = {
            "ok": sum(1 for row in readers if row.get("status") == "ok"),
            "failed": sum(
                1 for row in readers if row.get("status") in {"error", "skipped", "empty"}
            ),
            "pages": readers,
        }
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
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
    qa_ok = sum(1 for row in qas if row.get("status") == "ok")
    qa_failed = len(qas) - qa_ok

    if (
        assembly.get("status") == "ok"
        and translate_ok
        and not translate_failed
        and narrate_ok
        and not narrate_failed
        and speak_ok
        and not speak_failed
        and qa_ok
        and not qa_failed
        and ocr_ok
        and not ocr_failed
        and not layout_failed
    ):
        print("QA OK")
    elif qa_ok or speak_ok or narrate_ok or translate_ok or assembly.get("ok"):
        print("QA partial")
    else:
        print("QA failed")
    print(f"  input : {input_path}")
    print(f"  pages : {page_count} -> {pages_dir}")
    if from_stage and not should_run(from_stage, "ocr"):
        print(f"  ocr   : reused (--from {from_stage})")
    else:
        print(f"  ocr   : {ocr_ok} ok, {ocr_failed} failed")
    print(f"  layout: {layout_ok} ok, {layout_failed} failed")
    print(f"  assemble: {assembly.get('status')} -> {assembly.get('lesson_path')}")
    print(f"  translate: {translate_ok} ok, {translate_failed} failed")
    print(f"  narrate: {narrate_ok} ok, {narrate_failed} failed")
    print(f"  speak : {speak_ok} ok, {speak_failed} failed")
    print(f"  qa    : {qa_ok} ok, {qa_failed} failed")
    reader_ok = sum(1 for row in readers if row.get("status") == "ok")
    if readers:
        print(f"  reader: {reader_ok} ok")
        for row in readers:
            if row.get("status") == "ok" and row.get("path"):
                print(f"         open → {Path(row['path']).resolve()}")
    if from_stage:
        print(f"  from  : {from_stage}")
    src_note = (
        f"detected score={source_lang_score:.2f}"
        if source_lang_detected and source_lang_score is not None
        else ("detected" if source_lang_detected else "explicit")
    )
    print(f"  langs : {langs} (source={source_lang}, {src_note})")
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
    if qas and qa_failed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
