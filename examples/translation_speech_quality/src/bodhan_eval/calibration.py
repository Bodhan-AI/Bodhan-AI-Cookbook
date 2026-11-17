"""Fit evidence thresholds and score findings against held-out annotations."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace
from pathlib import Path

from .providers import digest
from .schema import CATEGORIES, SPEECH_CATEGORIES, TEXT_CATEGORIES, Evaluation, Finding, Item, Label, Span, parse_annotator, parse_labels, read_jsonl

DEFAULT_THRESHOLD = 0.72
TEXT_CATEGORY_SET = frozenset(TEXT_CATEGORIES)
# A threshold moves only with at least this many reviewed positive and negative items,
# so one or two labels cannot switch on weak evidence for a whole category.
MIN_SUPPORT = 3


def review_id(lang: str, item_id: str) -> str:
    """Return a stable review ID that hides the category in an injected item ID."""
    return digest(f"{lang}:{item_id}".encode())[:16]


def apply_external_labels(
    items: list[Item],
    path: Path,
) -> tuple[list[Item], list[dict]]:
    """Read external human labels by ID and language, retaining parse errors."""
    by_key = {item.key: item for item in items}
    by_review_id = {review_id(item.lang, item.id): item.key for item in items}
    source_splits = explicit_source_splits(items)
    by_id: dict[str, list] = {}
    for item in items:
        by_id.setdefault(item.id, []).append(item)
    invalid = []
    updated = set()
    if path.suffix.lower() == ".csv":
        # Answers from the review queue export (review_queue.csv).
        from .extras import calibration_rows_from_csv

        rows = ((number, row, None) for number, row in enumerate(calibration_rows_from_csv(path), 2))
    else:
        rows = read_jsonl(path)
    for line, row, error in rows:
        if error is None:
            try:
                opaque_id = row.get("review_id")
                if opaque_id is not None:
                    if not isinstance(opaque_id, str) or "id" in row:
                        raise ValueError("review_id must be a string and can replace id")
                    key = by_review_id.get(opaque_id)
                else:
                    item_id, lang = row.get("id"), row.get("lang")
                    if not isinstance(item_id, str) or (lang is not None and not isinstance(lang, str)):
                        raise ValueError("calibration row requires an id and an optional language code")
                    matches = by_id.get(item_id, [])
                    if lang is None and len(matches) > 1:
                        raise ValueError("lang is required for an id shared across languages")
                    key = (item_id, lang.lower()) if lang is not None else (matches[0].key if matches else None)
                if key not in by_key:
                    raise ValueError("calibration row does not match a selected dataset item")
                if key in updated:
                    raise ValueError("duplicate calibration assignment")
                labels = parse_labels(row.get("labels"))
                item = by_key[key]
                split = row.get("split")
                if split is None:
                    split = source_splits.get(item.source, "calibration")
                if split not in ("calibration", "holdout"):
                    raise ValueError("unknown calibration split")
                if item.source in source_splits and source_splits[item.source] != split:
                    raise ValueError("the source already belongs to a different calibration split")
                annotator = parse_annotator(row)
                source_splits[item.source] = split
                by_key[key] = replace(item, labels=labels, label_origin="human" if annotator else "provided", split=split, annotator=annotator)
                updated.add(key)
            except ValueError as problem:
                error = str(problem)
        if error is not None:
            invalid.append({"line": line, "error": error})
    return list(by_key.values()), invalid


def overlaps(left: Span, right: Span) -> bool:
    """Return true only for original spans on the same declared coordinate axis."""
    return left.field is not None and left.field == right.field and left.start < right.end and right.start < left.end


def accepted(candidates: list[Finding], thresholds: dict[str, float]) -> list[Finding]:
    """Apply the fitted category thresholds and deduplicate overlapping matching findings."""
    eligible = []
    for finding in candidates:
        if finding.strength >= thresholds[finding.category]:
            eligible.append(finding)
    ordered = sorted(eligible, key=lambda f: (-f.strength, f.category, f.span.field, f.span.start, f.span.end, f.check))
    distinct = []
    for finding in ordered:
        # One text passage gets one explanation: the strongest evidence wins, so a changed
        # digit is not reported again as a judge mistranslation of the same words.
        if not any(
            (other.category == finding.category or {other.category, finding.category} <= TEXT_CATEGORY_SET) and overlaps(finding.span, other.span)
            for other in distinct
        ):
            distinct.append(finding)
    return sorted(distinct, key=lambda f: (f.category, f.span.field, f.span.start, f.span.end, f.check))


def explicit_source_splits(items: list[Item]) -> dict[str, str]:
    """Reject contradictory splits before any item can make a paid request."""
    explicit = {}
    for item in items:
        if item.split:
            previous = explicit.setdefault(item.source, item.split)
            if previous != item.split:
                raise ValueError("The same source is marked for both calibration and holdout.")
    return explicit


def split_evaluations(results: list[Evaluation]) -> tuple[list[Evaluation], list[Evaluation]]:
    """Group all variants of a source into the same calibration or holdout set."""
    explicit = explicit_source_splits([result.item for result in results])
    calibration, holdout = [], []
    for result in results:
        item = result.item
        default = "calibration" if int(digest(item.source.encode())[:8], 16) % 3 == 0 else "holdout"
        destination = calibration if explicit.get(item.source, default) == "calibration" else holdout
        destination.append(result)
    return calibration, holdout


def pairs(labels: tuple[Label, ...], findings: list[Finding], *, same_category: bool) -> list[tuple[int, int]]:
    """Match localized evidence first, then assign only unused category-only pairs."""
    matches = {}
    edges = []
    for label in labels:
        related = []
        for index, finding in enumerate(findings):
            same = finding.category == label.category
            localized = label.span is not None and overlaps(label.span, finding.span)
            if localized and (same or not same_category):
                overlap = min(label.span.end, finding.span.end) - max(label.span.start, finding.span.start)
                related.append((not same, -overlap, index))
        edges.append([entry[2] for entry in sorted(related)])

    def assign(label_index: int, visited: set[int]) -> bool:
        """Find an unused candidate, moving an earlier match along an alternate edge if needed."""
        for finding_index in edges[label_index]:
            if finding_index not in visited:
                visited.add(finding_index)
                if finding_index not in matches or assign(matches[finding_index], visited):
                    matches[finding_index] = label_index
                    return True
        return False

    for index in range(len(labels)):
        assign(index, set())

    # A loose match must never reassign a localized finding. In strict scoring
    # only labels without a coordinate field can fall back to category alone.
    matched_labels = set(matches.values())
    unmatched_by_category = defaultdict(deque)
    for index, finding in enumerate(findings):
        if index not in matches:
            unmatched_by_category[finding.category].append(index)
    for index, label in enumerate(labels):
        known_field = label.span is not None and label.span.field is not None
        if index not in matched_labels and (not same_category or not known_field) and unmatched_by_category[label.category]:
            matches[unmatched_by_category[label.category].popleft()] = index
    return sorted((label_index, finding_index) for finding_index, label_index in matches.items())


def ratio(numerator: int, denominator: int) -> float | None:
    """Return a rounded proportion, with no value for an empty cohort."""
    return round(numerator / denominator, 5) if denominator else None


def fit_thresholds(calibration: list[Evaluation]) -> dict[str, float]:
    """Return fitted values for callers that do not produce a calibration report."""
    thresholds, _ = calibration_fit(calibration)
    return thresholds


def calibration_fit(calibration: list[Evaluation]) -> tuple[dict[str, float], dict[str, dict]]:
    """Tune categories on reviewed evidence and return the support behind every threshold."""
    reviewed = [result for result in calibration if result.item.labels is not None and result.item.label_origin != "injected"]
    thresholds = dict.fromkeys(CATEGORIES, DEFAULT_THRESHOLD)
    support = {}
    for category in CATEGORIES:
        examples = [
            (tuple(label for label in result.item.labels if label.category == category), [f for f in result.candidates if f.category == category])
            for result in reviewed
            if modality_checked(result, category)
        ]
        positives = sum(bool(labels) for labels, _ in examples)
        negatives = len(examples) - positives
        fitted = positives >= MIN_SUPPORT and negatives >= MIN_SUPPORT
        support[category] = {"positive_items": positives, "negative_items": negatives, "fitted": fitted}
        if not fitted:
            continue
        best = None
        for threshold in (0.58, 0.65, DEFAULT_THRESHOLD, 0.84, 0.92):
            true = false = missed = 0
            for labels, candidates in examples:
                findings = accepted(candidates, {category: threshold})
                matched = len(pairs(labels, findings, same_category=True))
                true += matched
                false += len(findings) - matched
                missed += len(labels) - matched
            # F0.5 weights precision, because false alarms are a specific quality target.
            score = 1.25 * true / (1.25 * true + false + 0.25 * missed)
            rank = (score, -abs(threshold - DEFAULT_THRESHOLD), threshold)
            if best is None or rank > best:
                best = rank
        thresholds[category] = best[2]
    return thresholds, support


def modality_checked(result: Evaluation, category: str) -> bool:
    """Tell whether the main checker for the labelled modality completed."""
    if category in SPEECH_CATEGORIES:
        check = "signal" if category == "garbled_audio" else "speech_alignment"
        return result.checks.get(check, {}).get("status") == "ok"
    return result.checks.get("judge", {}).get("status") == "ok" or (result.translation is not None and not result.translation.strip())


def measure(results: list[Evaluation], thresholds: dict[str, float]) -> dict:
    """Measure span-level detection, localization, categories, and clean false alarms."""
    counts = {category: {"tp": 0, "fp": 0, "fn": 0} for category in CATEGORIES}
    positives = detected = comparable = localized = correct_category = category_only = 0
    clean = clean_flags = labelled = clean_incomplete = 0
    for result in results:
        labels = result.item.labels
        if labels is None:
            continue
        labelled += 1
        findings = accepted(result.candidates, thresholds)
        positives += len(labels)
        if not labels:
            requested = []
            if result.item.translation is not None or result.translation is not None:
                requested.append(modality_checked(result, "mistranslation"))
            if result.item.audio is not None or result.audio_file is not None:
                requested.append(modality_checked(result, "speech_omission"))
            if requested and all(requested):
                clean += 1
                clean_flags += bool(findings)
            else:
                clean_incomplete += 1
        detected_pairs = pairs(labels, findings, same_category=False)
        detected += len(detected_pairs)
        for i, j in detected_pairs:
            label, finding = labels[i], findings[j]
            is_localized = label.span is not None and overlaps(label.span, finding.span)
            category_only += not is_localized
            correct_category += label.category == finding.category
            if label.span is not None and label.span.field is not None:
                comparable += 1
                localized += is_localized
        strict_pairs = pairs(labels, findings, same_category=True)
        matched_labels = {i for i, _ in strict_pairs}
        matched_findings = {j for _, j in strict_pairs}
        for i, label in enumerate(labels):
            counts[label.category]["tp" if i in matched_labels else "fn"] += 1
        for j, finding in enumerate(findings):
            if j not in matched_findings:
                counts[finding.category]["fp"] += 1
    for values in counts.values():
        values["precision"] = ratio(values["tp"], values["tp"] + values["fp"])
        values["recall"] = ratio(values["tp"], values["tp"] + values["fn"])
    return {
        "labelled_items": labelled,
        "labelled_errors": positives,
        "detected_errors": detected,
        "category_only_detections": category_only,
        "recall": ratio(detected, positives),
        "reviewed_clean_items": clean,
        "clean_false_alarms": clean_flags,
        "incomplete_clean_items": clean_incomplete,
        "false_alarm_rate": ratio(clean_flags, clean),
        "comparable_detections": comparable,
        "localized_detections": localized,
        "localization": ratio(localized, comparable),
        "correct_categories": correct_category,
        "category_accuracy": ratio(correct_category, detected),
        "per_category": counts,
    }


def measure_checks(results: list[Evaluation], thresholds: dict[str, float]) -> dict:
    """Show what the judge and deterministic checks contribute before combining them."""
    checks = sorted({finding.check for result in results for finding in result.candidates})
    checks = sorted(set(checks) | {"judge"})
    return {
        check: measure(
            [replace(result, candidates=[finding for finding in result.candidates if finding.check == check]) for result in results],
            thresholds,
        )
        for check in checks
    }
