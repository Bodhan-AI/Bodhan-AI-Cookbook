"""Stable evaluation reports and baseline comparison."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .calibration import accepted, calibration_fit, measure, measure_checks, ratio, split_evaluations
from .providers import digest
from .schema import Evaluation, read_jsonl


def write_json(path: Path, value: Any) -> None:
    """Write canonical readable JSON using UTF-8 and consistent line endings."""
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, values) -> None:
    """Write one stable JSON record per line, preserving Unicode spans."""
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def row_for(result: Evaluation, thresholds: dict[str, float]) -> dict:
    """Serialize evidence, actual modality coverage, and candidate provenance for one row."""
    item = result.item
    findings = accepted(result.candidates, thresholds)
    quality_checks = {"translation_structure", "signal", "speech_alignment", "judge"}
    attempted = [value["status"] for name, value in result.checks.items() if name in quality_checks]
    complete = any(status == "ok" for status in attempted)
    degraded = any(check["status"] in ("error", "skipped", "partial") for check in result.checks.values())
    status = "partial" if complete and degraded else "scored" if complete else "unscored"
    return {
        "id": item.id,
        "lang": item.lang,
        "line": item.line,
        "status": status,
        "source": item.source,
        "source_sha256": digest(item.source.encode()),
        "reference_sha256": digest(item.reference.encode()) if item.reference is not None else None,
        "translation": result.translation,
        "audio": result.audio_file,
        "audio_sha256": result.audio_digest,
        "checks": result.checks,
        "transcript": result.transcript,
        "annotation": {"origin": item.label_origin, "annotator": item.annotator} if item.labels is not None else None,
        "flags": [asdict(finding) for finding in findings],
        "unconfirmed": [
            asdict(finding)
            for finding in sorted(
                result.candidates,
                key=lambda value: (value.category, value.span.field, value.span.start, value.check),
            )
            if finding.strength < thresholds[finding.category]
        ],
    }


def match_baseline_flags(before: dict, current: dict) -> tuple[list, list, list]:
    """Pair defects by category and evidence on the same interval, allowing text offsets to move."""
    old, new = before["flags"], current["flags"]
    if not old or not new:
        return [], old, new
    alignments = {}
    for field in ("translation", "transcript"):
        left, right = before.get(field), current.get(field)
        if isinstance(left, str) and isinstance(right, str) and left != right:
            alignments[field] = SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks()

    def projected(span: dict) -> tuple[int, int] | None:
        """Project a span into the current row only when its coordinate system can be aligned."""
        field, start, end = span["field"], span["start"], span["end"]
        if field == "audio_ms":
            return (start, end) if before.get("audio_sha256") and before["audio_sha256"] == current.get("audio_sha256") else None
        if field in ("source", "reference") or (field in ("translation", "transcript") and before.get(field) == current.get(field)):
            return start, end
        for old_start, new_start, size in alignments.get(field, []):
            if old_start <= start < end <= old_start + size:
                shift = new_start - old_start
                return start + shift, end + shift
        return None

    # Sort by overlap first so each finding can be paired only once, with the
    # strongest positional evidence available in both reports.
    edges = []
    for index, flag in enumerate(old):
        span = flag["span"]
        interval = projected(span)
        if interval is None:
            continue
        start, end = interval
        for other_index, other in enumerate(new):
            other_span = other["span"]
            if flag["category"] == other["category"] and span["field"] == other_span["field"]:
                overlap = min(end, other_span["end"]) - max(start, other_span["start"])
                if overlap > 0:
                    edges.append((-overlap, index, other_index))
    matched_old, matched_new = set(), set()
    pairs = []
    for _, index, other_index in sorted(edges):
        if index not in matched_old and other_index not in matched_new:
            matched_old.add(index)
            matched_new.add(other_index)
            pairs.append((old[index], new[other_index]))
    return (
        pairs,
        [flag for index, flag in enumerate(old) if index not in matched_old],
        [flag for index, flag in enumerate(new) if index not in matched_new],
    )


@dataclass(frozen=True)
class Baseline:
    """Parsed report used for comparison after the new model work finishes."""

    configuration: dict
    items: dict[tuple[str, str], dict]


def load_baseline(path: Path) -> Baseline:
    """Validate and load the supplied baseline before the run can spend credit."""
    metadata = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or not isinstance(metadata.get("configuration"), dict):
        raise ValueError("Baseline report is missing its configuration.")
    previous = {}
    for line, row, error in read_jsonl(path / "items.jsonl"):
        if error or not isinstance(row.get("id"), str) or not isinstance(row.get("lang"), str):
            raise ValueError(f"Invalid baseline item at line {line}.")
        if (
            row.get("status") not in ("scored", "partial", "unscored")
            or not isinstance(row.get("checks"), dict)
            or any(not isinstance(check, dict) for check in row["checks"].values())
            or not isinstance(row.get("flags"), list)
        ):
            raise ValueError(f"Baseline item is missing check data or flags at line {line}.")
        for flag in row["flags"]:
            span = flag.get("span") if isinstance(flag, dict) else None
            if (
                not isinstance(span, dict)
                or not isinstance(span.get("field"), str)
                or type(span.get("start")) is not int
                or type(span.get("end")) is not int
                or not 0 <= span["start"] <= span["end"]
                or any(not isinstance(flag.get(name), str) for name in ("category", "severity", "evidence"))
            ):
                raise ValueError(f"Invalid baseline evidence at line {line}.")
        key = (row["id"], row["lang"])
        if key in previous:
            raise ValueError(f"Duplicate baseline item at line {line}.")
        previous[key] = row
    return Baseline(metadata["configuration"], previous)


def baseline_diff(rows: list[dict], baseline: Path | Baseline, current_config: dict) -> tuple[str, dict]:
    """Compare otherwise comparable rows and keep coverage changes visible."""
    loaded = load_baseline(baseline) if isinstance(baseline, Path) else baseline
    previous = loaded.items.copy()
    counts: Counter[str] = Counter()
    changes = []
    configuration_changed = loaded.configuration != current_config

    def coverage(row: dict) -> tuple:
        """Capture comparable scoring outcomes without comparing the fresh results."""
        names = ("translation_structure", "chrf", "back_translation", "signal", "transcription", "speech_alignment", "judge")
        return tuple(row.get("checks", {}).get(name, {}).get("status") for name in names)

    def flag_name(flag: dict) -> tuple[str, str]:
        """Use the assignment's category and severity as the stable count keys."""
        return flag["category"], flag["severity"]

    def describe(flags: Counter) -> str:
        """Format deterministic per-category counts for a readable example."""
        return ", ".join(f"{category} ({severity}) ×{count}" for (category, severity), count in sorted(flags.items()))

    for row in rows:
        key = row["id"], row["lang"]
        before = previous.pop(key, None)
        title = f"{row['id']} [{row['lang']}]"
        if before is None:
            counts["added_items"] += 1
            changes.append(f"- Added item {title}")
            continue
        if any(row.get(field) != before.get(field) for field in ("source_sha256", "reference_sha256")):
            counts["changed_source_or_reference"] += 1
            changes.append(f"- Source or reference changed for {title}")
            continue
        if coverage(row) != coverage(before) or row["status"] != "scored" or before.get("status") != "scored":
            counts["incomparable_coverage"] += 1
            changes.append(f"- Incomplete or changed check coverage for {title}")
            continue
        pairs, resolved, added = match_baseline_flags(before, row)
        if pairs:
            counts["persistent_flags"] += len(pairs)
        for old, new in pairs:
            if old["severity"] != new["severity"]:
                counts["severity_changes"] += 1
                changes.append(f"- Severity changed for {title}. {new['category']} went from {old['severity']} to {new['severity']}.")
        for status, flags, label in (("added_flags", added, "New"), ("resolved_flags", resolved, "Resolved")):
            if flags:
                counts[status] += len(flags)
                excerpt = " ".join(flags[0]["evidence"].split())[:160]
                changes.append(f"- {label} for {title}. {describe(Counter(map(flag_name, flags)))}. Evidence excerpt {excerpt!r}.")
    for item_id, lang in sorted(previous):
        counts["removed_items"] += 1
        changes.append(f"- Removed item {item_id} [{lang}]")
    lines = ["# Baseline comparison", ""]
    if configuration_changed:
        lines.extend(["Evaluation configuration changed. Deltas may reflect changed scoring as well as changed outputs.", ""])
    for key in (
        "added_flags",
        "resolved_flags",
        "persistent_flags",
        "severity_changes",
        "added_items",
        "removed_items",
        "changed_source_or_reference",
        "incomparable_coverage",
    ):
        lines.append(f"- {key.replace('_', ' ').capitalize()} **{counts[key]}**")
    lines.extend(["", "## Examples", "", *(changes[:40] or ["No changes in comparable findings."])])
    return "\n".join(lines) + "\n", dict(counts)


def summary_markdown(summary: dict) -> str:
    """Render reviewer-facing quality targets and evidence counts."""
    counts = summary["counts"]
    lines = [
        "# Bodhan quality report",
        "",
        f"Scored **{counts.get('scored', 0)}** items. "
        f"Partial **{counts.get('partial', 0)}**. Unscored **{counts.get('unscored', 0)}**. "
        f"Invalid input rows **{counts.get('invalid', 0)}**.",
        "",
        "## Findings by language",
        "",
        "| Language | Items | Confirmed findings |",
        "| --- | ---: | ---: |",
    ]
    for lang, data in summary["languages"].items():
        lines.append(f"| {lang} | {data['items']} | {sum(data['findings'].values())} |")
    lines.extend(["", "## Findings by category", "", "| Category | Flags | Held-out precision | Held-out recall |", "| --- | ---: | ---: | ---: |"])
    categories = summary["quality"]["heldout"]["per_category"]
    for category, count in summary["findings"].items():
        stats = categories[category]
        lines.append(f"| {category} | {count} | {percent(stats['precision'])} | {percent(stats['recall'])} |")
    seeded = summary["quality"]["injected_holdout"]
    consistency = summary["quality"]["judge_repeat"]
    lines.extend(["", "## Quality targets", "", "| Measure | Target | Observed |", "| --- | ---: | --- |"])
    for name, target, value, detail in (
        ("Seeded error recall", "≥80%", seeded["recall"], f"{seeded['detected_errors']} of {seeded['labelled_errors']} labelled errors"),
        (
            "Clean false alarms",
            "≤10%",
            seeded["false_alarm_rate"],
            f"{seeded['clean_false_alarms']} of {seeded['reviewed_clean_items']} associated clean items",
        ),
        (
            "Localization",
            "≥70%",
            seeded["localization"],
            f"{seeded['localized_detections']} of {seeded['comparable_detections']} detections with comparable labels",
        ),
        ("Category accuracy", "≥70%", seeded["category_accuracy"], f"{seeded['correct_categories']} of {seeded['detected_errors']} detections"),
        ("Fresh judge agreement", "≥90%", consistency["agreement"], f"{consistency['agreements']} of {consistency['compared']} comparisons"),
    ):
        lines.append(f"| {name} | {target} | {percent(value)} ({detail}) |")
    measured_misses = []
    for name, value, target, minimum in (
        ("recall", seeded["recall"], 0.8, True),
        ("false alarms", seeded["false_alarm_rate"], 0.1, False),
        ("localization", seeded["localization"], 0.7, True),
        ("category accuracy", seeded["category_accuracy"], 0.7, True),
        ("judge agreement", consistency["agreement"], 0.9, True),
    ):
        if value is not None and (value < target if minimum else value > target):
            measured_misses.append(name)
    if measured_misses:
        lines.extend(["", "Targets missed in this run: " + ", ".join(measured_misses) + "."])
    lines.extend(
        [
            "",
            "Unmeasured cohorts have no denominator. See calibration.json for false negatives, false positives, and coverage.",
            "Cache replay and the 200-item runtime target require separate execution measurements.",
            "",
            "## Evidence and cost",
            "",
            "- Confirmed and unconfirmed evidence appears in items.jsonl.",
            f"- Estimated Bodhan cost represented by successful unique requests **₹{summary['cost']['bodhan_inr']:.4f}**.",
            f"- Judge usage **{summary['cost']['judge_calls_with_usage']} of "
            f"{summary['cost']['calls_by_operation'].get('judge', 0)}** successful requests returned token counts.",
            "- Actual dispatch count, retry reservations, and elapsed time are in the run log.",
        ]
    )
    if "baseline" in summary:
        lines.append("- See diff.md for changes and comparable examples.")
    lines.extend(["", "## Findings to inspect first", ""])
    for row in summary["top_findings"]:
        lines.append(f"- {row['id']} [{row['lang']}] {row['category']} ({row['severity']}, {row['check']}). {row['evidence']!r}")
    if not summary["top_findings"]:
        lines.append("No confirmed findings in the checks that ran.")
    return "\n".join(lines) + "\n"


def percent(value: float | None) -> str:
    """Format a proportion without treating missing observations as zero."""
    return "Unmeasured" if value is None else f"{value:.1%}"


def write_reports(
    results: list[Evaluation],
    invalid: list[dict],
    invalid_labels: list[dict],
    output: Path,
    config: dict,
    baseline: Path | Baseline | None,
) -> None:
    """Emit the assignment outputs from deterministic evidence and fitted thresholds."""
    calibration, holdout = split_evaluations(results)
    thresholds, support = calibration_fit(calibration)
    config = {**config, "thresholds": thresholds}
    rows = [row_for(result, thresholds) for result in results]
    counts = Counter(row["status"] for row in rows)
    counts["invalid"] = len(invalid)
    languages = {}
    findings = Counter(dict.fromkeys(thresholds, 0))
    for row in rows:
        data = languages.setdefault(row["lang"], {"items": 0, "findings": Counter()})
        data["items"] += 1
        categories = Counter(flag["category"] for flag in row["flags"])
        data["findings"].update(categories)
        findings.update(categories)

    reviewed_ids = {result.item.source for result in holdout if result.item.seed_labels is not None}
    # Human labels can override evaluation labels without discarding the known injected edit.
    injected_scope = [
        replace(result, item=replace(result.item, labels=result.item.seed_labels)) if result.item.seed_labels is not None else result
        for result in holdout
        if result.item.seed_labels is not None or (result.item.labels == () and result.item.source in reviewed_ids)
    ]
    repeats = [r.checks["judge_repeat"]["agrees"] for r in results if r.checks.get("judge_repeat", {}).get("status") == "ok"]
    quality = {
        "calibration": measure(calibration, thresholds),
        "heldout": measure(holdout, thresholds),
        "heldout_by_language": {lang: measure([r for r in holdout if r.item.lang == lang], thresholds) for lang in sorted(languages)},
        "injected_holdout": measure(injected_scope, thresholds),
        "injected_holdout_by_language": {lang: measure([r for r in injected_scope if r.item.lang == lang], thresholds) for lang in sorted(languages)},
        "judge_repeat": {"compared": len(repeats), "agreements": sum(repeats), "agreement": ratio(sum(repeats), len(repeats))},
    }
    calls = {key: value for result in results for key, value in result.usage.items()}
    judge_calls = [call for call in calls.values() if call["operation"] == "judge"]
    reported_input = [call["input_tokens"] for call in judge_calls if call.get("input_tokens") is not None]
    reported_output = [call["output_tokens"] for call in judge_calls if call.get("output_tokens") is not None]
    severity_order = {"critical": 0, "major": 1, "minor": 2}
    top = sorted(
        ({"id": row["id"], "lang": row["lang"], **flag} for row in rows for flag in row["flags"]),
        key=lambda flag: (severity_order[flag["severity"]], -flag["strength"], flag["lang"], flag["id"]),
    )[:8]
    for flag in top:
        flag["evidence"] = " ".join(flag["evidence"].split())[:160]
    summary = {
        "schema_version": 1,
        "configuration": config,
        "counts": counts,
        "invalid_rows": invalid,
        "invalid_calibration_rows": invalid_labels,
        "languages": languages,
        "findings": findings,
        "quality": quality,
        "top_findings": top,
        "cost": {
            "bodhan_inr": sum(call.get("micro_inr", 0) for call in calls.values()) / 1_000_000,
            "judge_input_tokens": sum(reported_input) if reported_input else None,
            "judge_output_tokens": sum(reported_output) if reported_output else None,
            "judge_calls_with_usage": sum(call.get("input_tokens") is not None and call.get("output_tokens") is not None for call in judge_calls),
            "calls_by_operation": Counter(call["operation"] for call in calls.values()),
        },
    }
    diff = None
    if baseline is not None:
        diff, summary["baseline"] = baseline_diff(rows, baseline, config)
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "items.jsonl", rows)
    write_json(output / "summary.json", summary)
    write_json(
        output / "calibration.json",
        {
            "thresholds": thresholds,
            "tuning_support": support,
            "invalid_rows": invalid_labels,
            "human_heldout_by_check": measure_checks([r for r in holdout if r.item.label_origin != "injected"], thresholds),
            **quality,
        },
    )
    (output / "summary.md").write_text(summary_markdown(summary), encoding="utf-8", newline="\n")
    if diff is not None:
        (output / "diff.md").write_text(diff, encoding="utf-8", newline="\n")
    else:
        (output / "diff.md").unlink(missing_ok=True)
