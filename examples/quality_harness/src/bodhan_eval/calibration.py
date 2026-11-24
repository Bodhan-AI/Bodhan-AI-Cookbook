"""Calibration on explicit development labels and independent evaluation metrics."""

from collections import Counter

from .judge import CATEGORIES

ALIASES = {
    "term": "entity",
    "wrong_script": "wrong_language",
    "repetition": "speech_repetition",
    "truncation": "speech_omission",
    "speech_truncation": "speech_omission",
}


def category(label):
    value = label.get("type", label.get("category"))
    return ALIASES.get(value, value)


def overlaps(left, right):
    if "time_span" in left and "time_span" in right:
        return max(left["time_span"][0], right["time_span"][0]) < min(left["time_span"][1], right["time_span"][1])
    return (
        left.get("span_field", "translation") == right.get("span_field", "translation")
        and isinstance(left.get("span"), list)
        and isinstance(right.get("span"), list)
        and max(left["span"][0], right["span"][0]) < min(left["span"][1], right["span"][1])
    )


def matching(predictions, labels, same_category=True, require_overlap=False):
    """Maximum-cardinality bipartite matching prevents double-crediting labels."""
    edges = {}
    for i, prediction in enumerate(predictions):
        candidates = [
            j
            for j, label in enumerate(labels)
            if (not same_category or category(prediction) == category(label)) and (not require_overlap or overlaps(prediction, label))
        ]
        edges[i] = sorted(candidates, key=lambda j: not overlaps(prediction, labels[j]))
    owner = {}

    def augment(i, seen):
        # Prefer a free compatible label before disturbing an existing pair.
        # This preserves straightforward spatial matches when spans overlap.
        for j in edges[i]:
            if j not in seen and j not in owner:
                seen.add(j)
                owner[j] = i
                return True
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owner or augment(owner[j], seen):
                owner[j] = i
                return True
        return False

    for i in edges:
        augment(i, set())
    return [(i, j) for j, i in sorted(owner.items())]


def ratio(a, b):
    return a / b if b else None


def score(rows):
    counts = {name: Counter(tp=0, fp=0, fn=0) for name in sorted(CATEGORIES)}
    annotated = clean = false_alarm = detected_items = erroneous = detected_errors = seeded_errors = 0
    localized = localization_eligible = categorized = category_eligible = 0
    origins = Counter()
    for row in rows:
        if row.get("labels") is None:
            continue
        annotated += 1
        origins[row.get("label_origin", "unspecified")] += 1
        truth = row["labels"]
        predictions = row["flags"]
        if truth:
            erroneous += 1
            detected_items += bool(predictions)
            seeded_errors += len(truth)
        else:
            clean += 1
            false_alarm += bool(predictions)
        detection_pairs = matching(predictions, truth, same_category=False)
        detected_errors += len(detection_pairs)
        localized += len(matching(predictions, truth, same_category=False, require_overlap=True))
        localization_eligible += sum("span" in truth[j] or "time_span" in truth[j] for _, j in detection_pairs)
        pairs = matching(predictions, truth)
        for i, j in pairs:
            counts.setdefault(category(truth[j]), Counter(tp=0, fp=0, fn=0))["tp"] += 1
        for i, prediction in enumerate(predictions):
            if i not in {a for a, _ in pairs}:
                counts.setdefault(category(prediction), Counter(tp=0, fp=0, fn=0))["fp"] += 1
        for j, label in enumerate(truth):
            if j not in {b for _, b in pairs}:
                counts.setdefault(category(label), Counter(tp=0, fp=0, fn=0))["fn"] += 1
        # Independent localization matching avoids counting category correctness
        # as a prerequisite for category accuracy.
        for i, j in matching(predictions, truth, same_category=False, require_overlap=True):
            category_eligible += 1
            categorized += category(predictions[i]) == category(truth[j])
    metrics = {}
    for name, count in counts.items():
        tp, fp, fn = count["tp"], count["fp"], count["fn"]
        metrics[name] = {**dict(count), "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn), "support": tp + fn}
    return {
        "annotated_items": annotated,
        "annotation_origins": dict(sorted(origins.items())),
        "categories": metrics,
        "detection_recall": ratio(detected_errors, seeded_errors),
        "detected_errors": detected_errors,
        "seeded_errors": seeded_errors,
        "item_detection_recall": ratio(detected_items, erroneous),
        "detection_basis": "one-to-one within-item error assignment; category and localization measured separately",
        "false_alarm_rate": ratio(false_alarm, clean),
        "clean_items": clean,
        "error_items": erroneous,
        "localization_overlap": ratio(localized, localization_eligible),
        "localization_eligible_detections": localization_eligible,
        "category_accuracy": ratio(categorized, category_eligible),
        "category_eligible_detections": category_eligible,
    }


def tune(rows):
    """Choose category confidence cutoffs from labelled judge predictions only."""
    thresholds, supports = {}, {}
    for name in sorted(CATEGORIES):
        truth_count = sum(sum(category(label) == name for label in (r.get("labels") or [])) for r in rows)
        candidates = sorted({float(f["confidence"]) for r in rows for f in r["flags"] if f["check"] == "judge" and category(f) == name})
        supports[name] = {"labels": truth_count, "candidate_thresholds": len(candidates)}
        if not candidates:
            continue
        # A sentinel above one can suppress a category with only false positives.
        best = (-1, -1, -1)
        for cutoff in candidates + [1.000001]:
            tp = fp = fn = 0
            for row in rows:
                if row.get("labels") is None:
                    continue
                truth = [v for v in row["labels"] if category(v) == name]
                predictions = [f for f in row["flags"] if f["check"] == "judge" and category(f) == name and f["confidence"] >= cutoff]
                hits = len(matching(predictions, truth))
                tp += hits
                fp += len(predictions) - hits
                fn += len(truth) - hits
            f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0
            candidate = (f1, -fp, cutoff)
            if candidate > best:
                best = candidate
                thresholds[name] = cutoff
    return {
        "method": "maximum category F1, then fewer false positives, then higher cutoff",
        "thresholds": thresholds,
        "supports": supports,
        "scope": "judge confidence only; deterministic checks are unchanged",
    }


def apply_thresholds(rows, thresholds):
    for row in rows:
        retained = []
        for finding in row["flags"]:
            cutoff = thresholds.get(category(finding))
            if finding["check"] == "judge" and cutoff is not None and finding["confidence"] < cutoff:
                row["review"].append({**finding, "review_reason": "below_calibrated_threshold"})
            else:
                retained.append(finding)
        row["flags"] = retained


def merge_corroborating_flags(flags):
    """One defect supported by two checkers must not become two predictions."""
    merged = []
    for flag in flags:
        for existing in merged:
            source_pair = flag.get("span_field") == "source" and flag.get("span") == existing.get("evidence", {}).get("source_span")
            if category(flag) == category(existing) and (overlaps(flag, existing) or source_pair):
                existing.setdefault("corroborating_flags", []).append(flag)
                break
        else:
            merged.append(dict(flag))
    return merged


def individual_flags(flags):
    return [entry for flag in flags for entry in [flag] + flag.get("corroborating_flags", [])]


def report(rows, development_keys):
    explicit_development = [r for r in rows if (r["id"], r["lang"]) in development_keys and r.get("labels") is not None]
    dev = [r for r in explicit_development if r.get("label_origin", "").startswith("human:") or r.get("label_origin") == "provided_calibration"]
    dev_parents = {r.get("parent_id", r["id"]) for r in dev}
    excluded_parents = {r.get("parent_id", r["id"]) for r in explicit_development}
    held_out = [r for r in rows if r.get("parent_id", r["id"]) not in excluded_parents and r.get("split") != "calibration"]
    tuning = tune(dev)
    apply_thresholds(rows, tuning["thresholds"])
    for row in rows:
        row["flags"] = merge_corroborating_flags(row["flags"])
    return {
        "development_items": len(dev),
        "nonhuman_calibration_items_ignored": len(explicit_development) - len(dev),
        "development_parent_ids": sorted(dev_parents),
        "tuning": tuning,
        "evaluation": score(held_out),
        "languages": {lang: score([r for r in held_out if r["lang"] == lang]) for lang in sorted({r["lang"] for r in held_out})},
        "annotation_groups": {
            "human": score([r for r in held_out if r.get("label_origin", "").startswith("human:") or r.get("label_origin") == "provided_calibration"]),
            "injected": score([r for r in held_out if r.get("label_origin") == "programmatic_injection"]),
        },
        "per_check": {
            name: score([{**r, "flags": [f for f in individual_flags(r["flags"]) if f["check"] == name]} for r in held_out])
            for name in sorted({f["check"] for r in held_out for f in individual_flags(r["flags"])} | {"judge"})
        },
        "note": "Explicit calibration items and all siblings of their parent are excluded from held-out evaluation. "
        "Only attributable human labels or externally provided calibration labels tune thresholds. "
        "Without human calibration labels thresholds are not tuned. Missing labels are never treated as clean.",
    }
