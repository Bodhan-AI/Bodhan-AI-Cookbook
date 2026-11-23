import html
import json
import re
from pathlib import Path
from statistics import mean
from typing import Any

from .calibrate import bootstrap_mean_ci


def _markdown_text(value: Any) -> str:
    escaped = html.escape(str(value), quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>])", r"\\\1", escaped)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def write_outputs(
    out_dir: Path,
    item_records: list[dict[str, Any]],
    budget: Any,
    input_lines: int | None = None,
    seed: int = 20261119,
    calibration_result: dict[str, Any] | None = None,
    judge_consistency: dict[str, Any] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    item_records = sorted(item_records, key=lambda record: (record["id"], record["lang"]))

    total = len(item_records) if input_lines is None else input_lines
    states = [record["terminal_state"] for record in item_records]
    scored = states.count("scored")
    skipped_records = [record for record in item_records if record["terminal_state"] == "skipped"]
    failed_records = [record for record in item_records if record["terminal_state"] == "failed_checks"]
    quarantined_records = [record for record in item_records if record["terminal_state"] == "quarantined"]
    accounting_skipped = len(skipped_records) + len(failed_records)
    if scored + accounting_skipped + len(quarantined_records) != total:
        raise ValueError("terminal-state accounting does not equal nonblank input lines")

    by_language: dict[str, list[dict[str, Any]]] = {}
    for record in item_records:
        by_language.setdefault(record["lang"] or "unmapped", []).append(record)
    language_coverage = {}
    for lang, records in sorted(by_language.items()):
        lang_scored = sum(record["terminal_state"] == "scored" for record in records)
        language_coverage[lang] = {
            "scored": lang_scored,
            "total": len(records),
            "coverage": round(lang_scored / len(records), 6) if records else 0.0,
        }

    unchecked = [
        {
            "id": record["id"],
            "lang": record["lang"],
            "terminal_state": record["terminal_state"],
            "reason": record["skipped_reason"] or record["failed_checks"],
        }
        for record in item_records
        if record["terminal_state"] in {"skipped", "failed_checks"}
    ]
    reference_by_language: dict[str, list[dict[str, float]]] = {}
    for record in item_records:
        scores = record.get("reference_scores")
        if scores and "chrf" in scores and "bleu" in scores:
            reference_by_language.setdefault(record["lang"], []).append(scores)
    reference_summary = {}
    for lang, values in sorted(reference_by_language.items()):
        chrf_values = [value["chrf"] for value in values]
        bleu_values = [value["bleu"] for value in values]
        reference_summary[lang] = {
            "n": len(values),
            "chrf_mean": round(mean(chrf_values), 6),
            "chrf_ci95": list(bootstrap_mean_ci(chrf_values, seed)),
            "bleu_mean": round(mean(bleu_values), 6),
            "bleu_ci95": list(bootstrap_mean_ci(bleu_values, seed + 1)),
        }
    judge_records = [record["judge"] for record in item_records if "judge" in record]
    judge_summary = {
        "status": "integrated" if judge_records else "not_run",
        "items": len(judge_records),
        "complete": sum(record["status"] == "complete" for record in judge_records),
        "unavailable": sum(
            record["status"] in {"unavailable", "not_configured"}
            for record in judge_records
        ),
        "disabled": sum(record["status"] == "disabled" for record in judge_records),
        "consensus_findings": sum(
            record.get("consensus_findings", 0) for record in judge_records
        ),
    }
    summary = {
        "items": total,
        "accounting": {
            "input_lines": total,
            "scored": scored,
            "skipped": accounting_skipped,
            "quarantined": len(quarantined_records),
            "failed_checks": len(failed_records),
        },
        "coverage": {
            "overall": round(scored / total, 6) if total else 0.0,
            "per_language": language_coverage,
        },
        "unchecked": unchecked,
        "reference_scores": reference_summary,
        "judge": judge_summary,
        "judge_consistency": (
            judge_consistency
            if judge_consistency is not None
            else {
                "status": "not_measured",
                "reason": "cache-bypassed consistency sampling has not been run",
            }
        ),
        "flags": sum(len(record["flags"]) for record in item_records),
        "quarantined": len(quarantined_records),
        "skipped": accounting_skipped,
        "cost_inr": round(budget.spent, 6),
        "ledger": sorted(budget.ledger, key=lambda entry: (entry["stage"], entry["cost_inr"])),
        "skips": budget.skips,
        "checker_noise": {},
        "targets": (
            calibration_result
            if calibration_result is not None
            else {
                "status": "not_measured",
                "reason": "no labeled calibration dataset supplied",
            }
        ),
    }
    _write_text(out_dir / "summary.json", json.dumps(summary, sort_keys=True, ensure_ascii=False, indent=2) + "\n")

    markdown = [
        "# Bodhan Quality Evaluation",
        "",
        f"- Items: {summary['items']}",
        f"- Flags: {summary['flags']}",
        f"- Quarantined: {summary['quarantined']}",
        f"- Skipped: {summary['skipped']}",
        f"- Coverage: {summary['coverage']['overall']:.6f} ({scored}/{total})",
        f"- Failed checks: {len(failed_records)}",
        f"- Cost: INR {summary['cost_inr']}",
        f"- Judge: {judge_summary['complete']} complete, "
        f"{judge_summary['unavailable']} unavailable, "
        f"{judge_summary['disabled']} disabled",
        "",
        "## Targets",
        "",
        (
            "Held-out label evaluation is unavailable: no labeled calibration "
            "dataset was supplied."
            if calibration_result is None
            else (
                "Held-out metrics are reported below. Thresholds remain at "
                "documented defaults because detector score distributions are not "
                "yet exposed for tuning."
            )
        ),
        "",
        "## Coverage by language",
        "",
    ]
    for lang, coverage in language_coverage.items():
        markdown.append(
            f"- {lang}: {coverage['scored']}/{coverage['total']} "
            f"({coverage['coverage']:.6f})"
        )
    if unchecked:
        markdown.extend(["", "## Unchecked items", ""])
        for record in unchecked:
            markdown.append(
                f"- {_markdown_text(record['id'])} ({_markdown_text(record['lang'])}): "
                f"{record['terminal_state']} — {record['reason']}"
            )
    if reference_summary:
        markdown.extend([
            "",
            "## Reference-based scores (evidence only)",
            "",
            "| Language | n | chrF++ mean (95% bootstrap CI) | BLEU mean (95% bootstrap CI) |",
            "|---|---:|---:|---:|",
        ])
        for lang, scores in reference_summary.items():
            markdown.append(
                f"| {lang} | {scores['n']} | {scores['chrf_mean']:.6f} "
                f"({scores['chrf_ci95'][0]:.6f}, {scores['chrf_ci95'][1]:.6f}) "
                f"| {scores['bleu_mean']:.6f} "
                f"({scores['bleu_ci95'][0]:.6f}, {scores['bleu_ci95'][1]:.6f}) |"
            )
    if calibration_result is not None:
        markdown.extend([
            "",
            "## Held-out label metrics",
            "",
            f"- Evaluation status: {calibration_result['status']}",
            f"- Source groups: dev={calibration_result['support']['dev_sources']}, "
            f"test={calibration_result['support']['test_sources']}",
            f"- Test items: {calibration_result['support']['test_items']}",
        ])
        if calibration_result.get("reason"):
            markdown.append(f"- Reason: {calibration_result['reason']}")
        for annotator, language_metrics in calibration_result.get("metrics", {}).items():
            markdown.extend([
                "",
                f"### Annotator: {_markdown_text(annotator)}",
                "",
                "| Language | Category | Gold support | Predicted | Precision (95% Wilson CI) | Recall (95% Wilson CI) | Span overlap | Category accuracy |",
                "|---|---|---:|---:|---|---|---:|---:|",
            ])
            for lang, values in sorted(language_metrics.items()):
                for category, metric in sorted(values["per_category"].items()):
                    markdown.append(
                        f"| {_markdown_text(lang)} | {_markdown_text(category)} | "
                        f"{metric['support_positives']} | {metric['predicted']} | "
                        f"{metric['precision']:.6f} "
                        f"({metric['precision_ci95'][0]:.6f}, {metric['precision_ci95'][1]:.6f}) | "
                        f"{metric['recall']:.6f} "
                        f"({metric['recall_ci95'][0]:.6f}, {metric['recall_ci95'][1]:.6f}) | "
                        f"{metric['span_overlap_mean']:.6f} | — |"
                    )
                category_accuracy = values["category_accuracy"]
                markdown.append(
                    f"| {_markdown_text(lang)} | all categories (span-matched) | "
                    f"{category_accuracy['support']} | {category_accuracy['support']} | "
                    f"— | — | — | "
                    f"{category_accuracy['accuracy']:.6f} "
                    f"({category_accuracy['ci95'][0]:.6f}, "
                    f"{category_accuracy['ci95'][1]:.6f}) |"
                )
                markdown.append(
                    f"| {_markdown_text(lang)} | clean false alarms | — | "
                    f"{values['false_alarm_items']} / {values['clean_items']} | "
                    f"{values['false_alarm_rate']:.6f} "
                    f"({values['false_alarm_ci95'][0]:.6f}, "
                    f"{values['false_alarm_ci95'][1]:.6f}) | — | — | — |"
                )
    _write_text(out_dir / "summary.md", "\n".join(markdown) + "\n")

    with (out_dir / "items.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for record in item_records:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")

    calibration = {
        "thresholds": (
            {
                "status": calibration_result.get("threshold_status", "insufficient_support"),
                "origin": calibration_result.get("threshold_origin", "defaults_not_tuned"),
                "categories": calibration_result.get("thresholds", {}),
            }
            if calibration_result is not None
            else {
                "status": "not_tuned",
                "reason": "detector score distributions are not exposed",
            }
        ),
        "metrics": (
            calibration_result
            if calibration_result is not None
            else {
                "status": "not_measured",
                "reason": "no labeled calibration dataset supplied",
            }
        ),
        "judge_consistency": (
            judge_consistency
            if judge_consistency is not None
            else {
                "status": "not_measured",
                "reason": "cache-bypassed consistency sampling has not been run",
            }
        ),
        "threshold_origin": (
            calibration_result.get("threshold_origin", "documented_defaults")
            if calibration_result is not None
            else "documented_defaults"
        ),
        "generalization": (
            calibration_result.get("generalization", {})
            if calibration_result is not None
            else {}
        ),
    }
    _write_text(out_dir / "calibration.json", json.dumps(calibration, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
