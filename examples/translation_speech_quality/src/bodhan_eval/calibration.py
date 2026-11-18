"""Threshold selection and label evaluation. Labels never enter model prompts."""

import hashlib
from collections import Counter

from .checks import CATEGORIES


def metric(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall}


def counts(rows, labels, category, threshold):
    tp = fp = fn = 0
    for row in rows:
        if row["id"] not in labels:
            continue
        truth = any(label["type"] == category for label in labels[row["id"]])
        guess = any(not f.get("review_only") and f["type"] == category and f["confidence"] >= threshold for f in row["candidates"])
        tp += truth and guess
        fp += guess and not truth
        fn += truth and not guess
    return metric(tp, fp, fn)


def calibrate(rows, label_records):
    # Only explicit human annotations tune thresholds. Source-family groups
    # keep a clean item and its injected variants on the same side of the split.
    human = {r["id"]: r for r in label_records if r.get("annotation_source", "human") == "human"}
    train, holdout = [], []
    for row in rows:
        if row["id"] not in human:
            continue
        group = str(row.get("group_id") or row["id"])
        bucket = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 3
        (holdout if bucket == 0 else train).append(row)
    labels = {key: value["labels"] for key, value in human.items()}
    thresholds = {category: 0.9 for category in CATEGORIES}
    choices = {}
    for category in CATEGORIES:
        positives = sum(any(label["type"] == category for label in labels[row["id"]]) for row in train)
        negatives = len(train) - positives
        if positives < 2 or negatives < 2:
            choices[category] = {"status": "insufficient_human_labels", "threshold": 0.9}
            continue
        options = []
        for threshold in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0):
            m = counts(train, labels, category, threshold)
            precision, recall = m["precision"] or 0, m["recall"] or 0
            f_beta = 5 * precision * recall / (4 * precision + recall) if precision + recall else 0
            options.append((f_beta, precision, threshold))
        thresholds[category] = max(options)[2]
        choices[category] = {"status": "fit_on_human_training_split", "threshold": thresholds[category],
                             "training": counts(train, labels, category, thresholds[category]),
                             "held_out": counts(holdout, labels, category, thresholds[category])}
    for category in CATEGORIES:
        choices[category]["all_human_items"] = counts(rows, labels, category, thresholds[category])
    checks = sorted({f["check"] for row in rows for f in row["candidates"]})
    per_check = {check: {category: counts(
        [{**row, "candidates": [f for f in row["candidates"] if f["check"] == check]} for row in rows],
        labels, category, thresholds[category]) for category in CATEGORIES} for check in checks}
    return thresholds, {"human_items": len(human), "training_items": len(train), "per_check": per_check,
                        "held_out_items": len(holdout), "categories": choices,
                        "note": "Default 0.9 is an uncalibrated review policy where human labels are insufficient."}


def evaluate(rows, records):
    labels = {r["id"]: r["labels"] for r in records if isinstance(r.get("labels"), list)}
    categories = {}
    for category in CATEGORIES:
        converted = [{**row, "candidates": row.get("flags", [])} for row in rows]
        categories[category] = counts(converted, labels, category, 0)
    clean = [r for r in rows if r["id"] in labels and not labels[r["id"]]]
    dirty = [r for r in rows if labels.get(r["id"])]
    detected = [r for r in dirty if r.get("flags")]
    localized = 0
    correct = 0
    for row in detected:
        truth = labels[row["id"]]
        correct += any(f["type"] == t["type"] for f in row["flags"] for t in truth)
        localized += any(
            f["type"] == t["type"] and f["field"] == t.get("field", "translation")
            and max(f["span"][0], t["span"][0]) < min(f["span"][1], t["span"][1])
            for f in row["flags"] for t in truth if "span" in t
        )
    return {"labelled_items": len(labels), "clean_items": len(clean), "seeded_items": len(dirty),
            "detection_recall": len(detected) / len(dirty) if dirty else None,
            "false_alarm_rate": sum(bool(r.get("flags")) for r in clean) / len(clean) if clean else None,
            "category_accuracy": correct / len(detected) if detected else None,
            "localization_overlap": localized / len(detected) if detected else None,
            "per_category": categories,
            "annotation_sources": dict(Counter(r.get("annotation_source", "unspecified") for r in records))}
