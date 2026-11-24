"""Pipeline driver: OCR, assembly and translation, with a report of what happened."""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from .assemble import run_assemble
from .ocr_stage import collect_pages, run_ocr
from .translate_stage import run_translate

NOT_IMPLEMENTED = ["narration script", "speech synthesis", "speech quality check (back-transcription)"]


def run_pipeline(client, input_path: Path, langs: list[str], out_dir: Path,
                 max_pages: int | None = None, workers: int = 4) -> dict:
    """Run every implemented stage and write out/report.json. Never raises for a bad page or language."""
    started = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"input": str(input_path), "languages": langs, "stages": {}, "problems": []}

    # Stage 1: OCR
    t = time.time()
    images = collect_pages(Path(input_path), out / "work" / "page_images", max_pages)
    records = run_ocr(client, images, out / "source" / "pages")
    failed_pages = [r["page"] for r in records if r["status"] != "ok"]
    ocr_cost = client.costs.summary()["total_inr"]
    report["stages"]["ocr"] = {
        "pages": len(records), "failed_pages": failed_pages, "seconds": round(time.time() - t, 1),
        "pages_with_split": [r["page"] for r in records if r.get("notes")],
        "cost_inr": ocr_cost, "cost_inr_per_page": round(ocr_cost / max(1, len(records)), 4),
    }
    if failed_pages:
        report["problems"].append(f"OCR failed on pages {failed_pages}")

    # Stage 2: assembly
    t = time.time()
    if len(failed_pages) == len(records):
        report["problems"].append("No page could be read, so nothing was assembled.")
        langs = []
    else:
        asm = run_assemble(out / "source")
        report["stages"]["assembly"] = {
            "seconds": round(time.time() - t, 1),
            "dropped_by_reason": dict(Counter(d["reason"] for d in asm["dropped"])),
            "merged_across_pages": asm["merged_across_pages"],
            "flagged_elements": asm["flagged"],
            "labels_seen": asm["labels_seen"],
        }

    # Stage 3: translation, one language at a time, a failure in one never stops the others
    report["stages"]["translation"] = {}
    for lang in langs:
        t = time.time()
        before = client.costs.summary()["total_inr"]
        try:
            rep = run_translate(client, out, lang, workers)
            rep["seconds"] = round(time.time() - t, 1)
            rep["cost_inr"] = round(client.costs.summary()["total_inr"] - before, 5)
            report["stages"]["translation"][lang] = rep
            if rep["translation_issues"]:
                report["problems"].append(f"{lang}: {len(rep['translation_issues'])} pieces kept in English")
            if not rep["structure"]["same_order_and_types"] or not rep["structure"]["equations_identical"]:
                report["problems"].append(f"{lang}: structure check failed")
        except Exception as exc:  # noqa: BLE001 - report it and carry on with the next language
            report["stages"]["translation"][lang] = {"error": str(exc)}
            report["problems"].append(f"{lang}: translation failed: {exc}")

    report["not_implemented"] = NOT_IMPLEMENTED
    report["cost"] = client.costs.summary()
    report["total_seconds"] = round(time.time() - started, 1)
    report["ok"] = not report["problems"]
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report