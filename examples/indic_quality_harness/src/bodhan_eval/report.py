import json
from collections import Counter
from pathlib import Path


def build_report(results: list[dict]) -> dict:
    checks = [
        check
        for item in results
        for check in item["checks"]
    ]

    failed = [c for c in checks if c.get("status") == "fail" or (
        "status" not in c and c.get("passed") is False
    )]

    reviews = [c for c in checks if c.get("status") == "review"]

    category_counts = Counter(
        check["category"] for check in failed
    )
    review_counts = Counter(
        check["category"] for check in reviews
    )

    language_counts = Counter(
        item["language"] for item in results
    )

    return {
        "total_items": len(results),
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "review_checks": len(reviews),
        "passed_checks": len(checks) - len(failed) - len(reviews),
        "languages": dict(language_counts),
        "errors_by_category": dict(category_counts),
        "reviews_by_category": dict(review_counts),
        "items": results,
    }


def save_report(report: dict, output_dir: str) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    report_file = output_path / "summary.json"
    report_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return report_file


def build_markdown_report(
    report: dict,
    calibration: dict | None = None,
) -> str:
    """Build a readable Markdown evaluation report."""
    items = report.get("items", [])

    lines = [
        "# Indic Translation & Speech Quality Report",
        "",
        "## Overall summary",
        "",
        f"- Total items: **{report['total_items']}**",
        f"- Total checks: **{report['total_checks']}**",
        f"- Passed checks: **{report['passed_checks']}**",
        f"- Failed checks: **{report['failed_checks']}**",
        f"- Checks requiring review: **{report['review_checks']}**",
        "",
        "## Language breakdown",
        "",
        "| Language | Items | Failed checks | Review checks |",
        "|---|---:|---:|---:|",
    ]

    for language, item_count in sorted(report.get("languages", {}).items()):
        language_items = [
            item for item in items
            if item.get("language") == language
        ]

        language_checks = [
            check
            for item in language_items
            for check in item.get("checks", [])
        ]

        failed_count = sum(
            check.get("status") == "fail"
            or (
                "status" not in check
                and check.get("passed") is False
            )
            for check in language_checks
        )

        review_count = sum(
            check.get("status") == "review"
            for check in language_checks
        )

        lines.append(
            f"| {language} | {item_count} | "
            f"{failed_count} | {review_count} |"
        )

    lines.extend([
        "",
        "## Errors by category",
        "",
        "| Category | Failed checks |",
        "|---|---:|",
    ])

    errors = report.get("errors_by_category", {})

    if errors:
        for category, count in sorted(errors.items()):
            lines.append(f"| {category} | {count} |")
    else:
        lines.append("| No errors detected | 0 |")

    lines.extend([
        "",
        "## Failed items and evidence",
        "",
    ])

    failed_items_found = False

    for item in items:
        failed_checks = [
            check for check in item.get("checks", [])
            if check.get("status") == "fail"
            or (
                "status" not in check
                and check.get("passed") is False
            )
        ]

        if not failed_checks:
            continue

        failed_items_found = True
        lines.append(
            f"### {item.get('id', 'unknown')} "
            f"({item.get('language', 'unknown')})"
        )
        lines.append("")

        for check in failed_checks:
            lines.append(
                f"- **{check.get('category', 'unknown')}** "
                f"— severity: {check.get('severity', 'unspecified')}"
            )
            evidence = check.get("evidence", {})
            lines.append(f"  - Evidence: `{json.dumps(evidence, ensure_ascii=False)}`")

        lines.append("")

    if not failed_items_found:
        lines.append("No failed items.")
        lines.append("")

    reviews = report.get("reviews_by_category", {})
    if reviews:
        lines.extend([
            "## Checks requiring review, by category",
            "",
            "Review means the check could not prove an error (for example a",
            "transliterated name, a paraphrase, or an LLM finding). These are",
            "not counted as failures.",
            "",
            "| Category | Review checks |",
            "|---|---:|",
        ])
        for category, count in sorted(reviews.items()):
            lines.append(f"| {category} | {count} |")
        lines.append("")

    lines.extend(_run_sections(report.get("run")))
    lines.extend(_baseline_section(report.get("baseline_comparison")))

    if calibration is not None:
        metrics = calibration.get("metrics", {}).get("overall", {})

        lines.extend([
            "## Calibration",
            "",
            "| Metric | Score |",
            "|---|---:|",
            f"| Precision | {metrics.get('precision', 0):.2%} |",
            f"| Recall | {metrics.get('recall', 0):.2%} |",
            f"| F1 | {metrics.get('f1', 0):.2%} |",
            f"| True positives | {metrics.get('tp', 0)} |",
            f"| False positives | {metrics.get('fp', 0)} |",
            f"| False negatives | {metrics.get('fn', 0)} |",
            "",
        ])

        lines.extend([
            "### Per-category calibration",
            "",
            "| Category | Precision | Recall | F1 |",
            "|---|---:|---:|---:|",
        ])

        for category, values in sorted(
            calibration.get("metrics", {}).get("categories", {}).items()
        ):
            lines.append(
                f"| {category} | "
                f"{values.get('precision', 0):.2%} | "
                f"{values.get('recall', 0):.2%} | "
                f"{values.get('f1', 0):.2%} |"
            )

        lines.append("")
        lines.extend(_calibration_modes_section(calibration))

    lines.extend([
        "## Limitations",
        "",
        "- Synthetic calibration results do not establish real-world accuracy.",
        "- Reference-based checks require a clean reference translation.",
        "- A review status is not counted as a confirmed failure.",
        "- LLM judge findings are review-only: a small local model produces",
        "  semantic false positives, and schema validity does not imply accuracy.",
        "- Spend is an estimate from configured per-unit rates.",
        "",
    ])

    return "\n".join(lines)


def save_markdown_report(
    report: dict,
    output_dir: str,
    calibration: dict | None = None,
) -> Path:
    """Save the Markdown report."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    report_file = output_path / "summary.md"
    report_file.write_text(
        build_markdown_report(report, calibration),
        encoding="utf-8",
    )

    return report_file


def _fmt(value, pct=False):
    if value is None:
        return "n/a"
    return f"{value:.2%}" if pct else str(value)


def _run_sections(run: dict | None) -> list[str]:
    if not run:
        return []

    lines = []
    config = run.get("config", {})
    enabled = [
        flag for flag, on in [
            ("--generate", config.get("generate")),
            ("--back-translate", config.get("back_translate")),
            ("--speech", config.get("speech")),
            ("--llm-judge", config.get("llm_judge")),
            ("--calibration", config.get("calibration")),
            ("--baseline", config.get("baseline")),
        ] if on
    ]
    lines.extend([
        "## Run configuration",
        "",
        f"- Dataset: `{config.get('dataset')}`",
        f"- Languages: {', '.join(config.get('languages', []))}",
        f"- Stages enabled: {', '.join(enabled) if enabled else 'deterministic checks only'}",
    ])
    if config.get("speech"):
        lines.append(f"- Speech limit: {config.get('speech_limit') or 'all'}")
    generation = run.get("generation") or {}
    if generation:
        lines.append(f"- Translations generated: {generation}")
    lines.append("")

    cost = run.get("cost") or {}
    if cost:
        lines.extend([
            "## Cost",
            "",
            f"- Budget: ₹{_fmt(cost.get('max_inr'))}",
            f"- Estimated spend: ₹{cost.get('estimated_spend_inr', 0)}",
            f"- Paid calls skipped by the budget guard: {cost.get('blocked_calls', 0)}",
            f"- {cost.get('note', '')}",
        ])
        for service, values in (cost.get("by_service") or {}).items():
            lines.append(f"- {service}: {values['calls']} call(s), ₹{values['inr']}")
        lines.append("")

    caches = run.get("caches") or {}
    if caches:
        lines.extend(["## Caches", "", "| Cache | Entries | Hits | Misses |", "|---|---:|---:|---:|"])
        for name, stats in caches.items():
            lines.append(f"| {name} | {stats.get('entries')} | {stats.get('hits')} | {stats.get('misses')} |")
        lines.append("")

    back = run.get("back_translation")
    if back:
        lines.extend([
            "## Back-translation (reference-free)",
            "",
            "Each Indic translation is translated back to English and compared",
            "with the original source. The translation itself is never used as",
            "a reference. Low similarity is a review signal: either direction",
            "of translation can introduce the difference.",
            "",
            f"- Items: {back['items']}",
            f"- Mean chrF (back-translation vs source): {_fmt(back['mean_chrf'])}",
            f"- Minimum chrF: {_fmt(back['min_chrf'])}",
            f"- Flagged for review: {back['flagged_for_review']}",
            "",
        ])

    speech = run.get("speech")
    if speech:
        lines.extend([
            "## Speech (Indic-Speak -> Indic-Transcribe)",
            "",
            f"- Records selected: {speech['selected']}, evaluated: {speech['evaluated']}, "
            f"cache hits: {speech['cache_hits']}",
            "",
            "| Check | pass | review | fail |",
            "|---|---:|---:|---:|",
        ])
        for category, statuses in speech["check_status"].items():
            lines.append(
                f"| {category} | {statuses.get('pass', 0)} | "
                f"{statuses.get('review', 0)} | {statuses.get('fail', 0)} |"
            )
        lines.append("")

    judge = run.get("llm_judge")
    if judge:
        lines.extend([
            "## LLM judge (review-only)",
            "",
            f"- Model: `{judge['model']}` (prompt {judge['prompt_version']}, hash `{judge['prompt_hash']}`)",
            f"- Availability: {judge['availability']}"
            + (f" ({judge['availability_error']})" if judge.get("availability_error") else ""),
            f"- Items judged: {judge['items_judged']} (from cache: {judge['cached_results']})",
            f"- Status counts: {judge['status_counts']}",
            f"- Items with at least one finding: {judge['items_with_findings']}",
            f"- Findings by category: {judge['findings_by_category'] or 'none'}",
            f"- Findings by severity: {judge['findings_by_severity'] or 'none'}",
            f"- Findings rejected by validation: {judge['rejected_findings'] or 'none'}",
            "",
            "The judge labels errors only; it never produces translations.",
            "Findings are not counted as failures. Check the calibration",
            "section for its measured precision and false-alarm rate.",
            "",
        ])

    errors = run.get("errors") or {}
    if errors:
        lines.extend(["## Stage errors", "", "| Stage:type | Items |", "|---|---:|"])
        for key, count in sorted(errors.items()):
            lines.append(f"| {key} | {count} |")
        lines.append("")

    return lines


def _baseline_section(baseline: dict | None) -> list[str]:
    if not baseline:
        return []

    totals = baseline["totals"]
    lines = [
        "## Baseline comparison",
        "",
        f"- Failed checks: {totals['baseline'].get('failed_checks')} -> {totals['current'].get('failed_checks')}",
        f"- Review checks: {totals['baseline'].get('review_checks')} -> {totals['current'].get('review_checks')}",
        f"- Regressions (newly failing item/category): **{baseline['regression_count']}**",
        f"- Fixes (no longer failing): **{baseline['fix_count']}**",
        f"- New items: {len(baseline['new_items'])}, missing items: {len(baseline['missing_items'])}",
        "",
    ]
    if baseline["category_delta"]:
        lines.extend(["| Category | Baseline | Current | Delta |", "|---|---:|---:|---:|"])
        for category, values in baseline["category_delta"].items():
            lines.append(f"| {category} | {values['baseline']} | {values['current']} | {values['delta']:+d} |")
        lines.append("")
    for entry in baseline["regressions"][:50]:
        lines.append(f"- Regression: {entry['id']} — {entry['category']}")
    if baseline["regressions"]:
        lines.append("")
    return lines


def _calibration_modes_section(calibration: dict) -> list[str]:
    modes = calibration.get("modes") or {}
    if not modes:
        return []

    lines = [
        "### Calibration by detector mode",
        "",
        "| Mode | Cases | Precision | Recall | F1 | Clean-case false alarms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode, metrics in modes.items():
        if not metrics:
            lines.append(f"| {mode} | not run | | | | |")
            continue
        overall = metrics["overall"]
        lines.append(
            f"| {mode} | {overall.get('cases')} | {_fmt(overall['precision'], True)} | "
            f"{_fmt(overall['recall'], True)} | {_fmt(overall['f1'], True)} | "
            f"{overall.get('clean_false_alarms')}/{overall.get('clean_cases')} |"
        )
    lines.append("")

    for mode, metrics in modes.items():
        if not metrics or mode == "reference_based":
            continue
        lines.extend([f"#### {mode} per category", "", "| Category | TP | FP | FN | Precision | Recall |",
                      "|---|---:|---:|---:|---:|---:|"])
        for category, values in sorted(metrics["categories"].items()):
            lines.append(
                f"| {category} | {values['tp']} | {values['fp']} | {values['fn']} | "
                f"{_fmt(values['precision'], True)} | {_fmt(values['recall'], True)} |"
            )
        lines.append("")

    lines.extend([
        "Reference-based mode compares against the clean translation and is",
        "an upper bound. Reference-free mode is what runs in production when",
        "no reference exists. Synthetic single-edit errors are easier than",
        "real ones, so treat all of these numbers as optimistic.",
        "",
    ])
    return lines
