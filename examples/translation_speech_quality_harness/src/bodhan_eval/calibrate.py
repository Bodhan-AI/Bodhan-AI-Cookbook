"""Calibration against human labels.

Labels come from the dataset rows (`labels`) and from `--calibration` (same row format; it wins for the same id and lang).
`"labels": []` means labelled clean; no `labels` key means unlabelled.

1. fit_thresholds: for each check, the lowest minimum candidate strength that keeps that check's false alarms on
   labelled-clean items under its share of the false-alarm budget. Shrinkage: with too few labels the default stays.
2. apply: drop candidates below their check's threshold, for the whole run.
3. evaluate: precision, recall, false alarms and localization of the final flags, with Wilson intervals.
"""

import json
import math
import re
from pathlib import Path

from .config import normalize_lang
from .dataset import _labels

# Structural constants, not tuned on any data: see DESIGN.md.
MIN_CLEAN = 10  # labelled-clean items needed before a threshold may move
MIN_POSITIVE = 5  # labelled errors a check must touch before its threshold may move
FA_CAP_PER_CHECK = 0.02  # each check's share of the brief's 10% false-alarm budget

ALIASES = {
    "number": "number", "number_or_unit": "number", "numbers": "number", "unit": "number", "number_unit": "number",
    "format": "format", "formatting": "format", "markdown": "format", "latex": "format", "format_markdown_latex": "format",
    "wrong_language": "wrong_language", "wrong_script": "wrong_language", "untranslated": "wrong_language",
    "wrong_language_or_script": "wrong_language", "language": "wrong_language", "script": "wrong_language",
    "omission": "omission", "addition": "addition", "mistranslation": "mistranslation",
    "term": "term", "terminology": "term", "entity": "term", "term_or_entity": "term", "named_entity": "term",
    "speech_omission": "speech_omission", "speech_truncation": "speech_omission", "truncation": "speech_omission",
    "omission_or_truncation": "speech_omission", "speech_omission_or_truncation": "speech_omission",
    "speech_repetition": "speech_repetition", "repetition": "speech_repetition",
    "garbled": "garbled", "garbled_audio": "garbled", "silence": "garbled", "speech_garbled": "garbled",
}


def canon(name: str) -> str:
    """Map a label type to our category name. Unknown names are kept (normalized), never dropped."""
    key = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return ALIASES.get(key, key)


def read_label_file(path: str | Path) -> tuple[dict, list[str]]:
    """{(id, lang): labels} from a JSONL file of rows with id, lang and labels. Bad rows are skipped with a warning."""
    out, warnings = {}, []
    try:
        lines = Path(path).read_bytes().decode("utf-8-sig", errors="replace").splitlines()
    except OSError as e:
        return {}, [f"--calibration: cannot read {path}: {e}"]
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            key = (str(row["id"]), normalize_lang(row["lang"]))
        except (ValueError, KeyError, TypeError):
            bad += 1
            continue
        labels = _labels(row.get("labels"), [])
        if key[1] is None or labels is None:
            bad += 1
            continue
        out[key] = labels
    if bad:
        warnings.append(f"--calibration: {bad} rows skipped (need id, lang and a labels list)")
    return out, warnings


def collect(items: list[dict], path: str | None) -> tuple[dict, dict, list[str]]:
    """Returns ({(id, lang): [labels with canonical 'category']}, label_sources, warnings)."""
    labels, sources = {}, {"dataset_inline": 0, "calibration_file": 0}
    for it in items:
        if it.get("labels") is not None:
            labels[(it["id"], it["lang"])] = it["labels"]
            sources["dataset_inline"] += 1
    warnings = []
    if path:
        file_labels, warnings = read_label_file(path)
        present = {(it["id"], it["lang"]) for it in items}
        for key, labs in file_labels.items():
            if key in present:  # labels for items not in this run cannot be scored
                labels[key] = labs
                sources["calibration_file"] += 1
    labels = {k: [{**lab, "category": canon(lab["type"])} for lab in v] for k, v in labels.items()}
    return labels, sources, warnings


def _overlap(a, b):
    return a is not None and b is not None and a[0] < b[1] and b[0] < a[1]


def hits(thing: dict, label: dict) -> bool:
    """A candidate or flag matches a label if its span overlaps the label's on either side; a label without a span
    is matched by category."""
    if label.get("span") is None:
        return thing["category"] == label["category"]
    return any(_overlap(thing.get(f), label["span"]) for f in ("span", "source_span", "translation_span"))


def fit_thresholds(results: list[dict], labels: dict) -> dict:
    """Per check: {'default', 'calibrated', 'moved', 'reason', 'labelled_errors_touched', 'clean_items'}."""
    by_key = {(r["id"], r["lang"]): r["_cands"] for r in results}
    clean = [k for k, v in labels.items() if v == [] and k in by_key]
    errs = [(k, lab) for k, v in labels.items() if v and k in by_key for lab in v]
    checks = sorted({c["check"] for cands in by_key.values() for c in cands})
    out = {}
    for check in checks:
        pos = [max((c["strength"] for c in by_key[k] if c["check"] == check and hits(c, lab)), default=None) for k, lab in errs]
        pos = [s for s in pos if s is not None]
        neg = [max((c["strength"] for c in by_key[k] if c["check"] == check), default=None) for k in clean]
        neg = [s for s in neg if s is not None]
        row = {"default": 0.0, "calibrated": 0.0, "moved": False, "labelled_errors_touched": len(pos), "clean_items": len(clean),
               "clean_items_fired": len(neg)}
        if len(clean) < MIN_CLEAN or len(pos) < MIN_POSITIVE:
            row["reason"] = f"kept default: needs {MIN_CLEAN} labelled-clean items and {MIN_POSITIVE} labelled errors it touches"
        else:
            # False alarms only fall as the threshold rises, so the lowest threshold under the cap catches the most.
            for t in [0.0] + sorted({round(s + 0.001, 3) for s in neg}):
                if sum(s >= t for s in neg) / len(clean) <= FA_CAP_PER_CHECK:
                    break
            row.update(calibrated=t, moved=t > 0,
                       reason=f"lowest threshold with false alarms on clean items <= {FA_CAP_PER_CHECK:.0%}; "
                              f"keeps {sum(s >= t for s in pos)} of {len(pos)} labelled errors")
        out[check] = row
    return out


def apply(cands: list[dict], thresholds: dict) -> list[dict]:
    return [c for c in cands if c["strength"] >= thresholds.get(c["check"], {}).get("calibrated", 0.0)]


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, mid - half), 3), round(min(1.0, mid + half), 3)]


def _rate(k, n):
    return round(k / n, 3) if n else None


def evaluate(results: list[dict], labels: dict, thresholds: dict, sources: dict, span_side: str) -> dict:
    """Score the final flags against the labels. Detection is per item; localization and category per label."""
    by_key = {(r["id"], r["lang"]): r for r in results}
    keys = [k for k in sorted(labels) if k in by_key]
    clean = [k for k in keys if labels[k] == []]
    with_err = [k for k in keys if labels[k]]
    flagged = [k for k in keys if by_key[k]["flags"]]
    fa = [k for k in clean if by_key[k]["flags"]]
    detected = [k for k in with_err if by_key[k]["flags"]]

    per_cat, n_labels, n_local, n_cat_ok = {}, 0, 0, 0
    for k in with_err:
        flags = by_key[k]["flags"]
        for lab in labels[k]:
            row = per_cat.setdefault(lab["category"], {"tp": 0, "fp": 0, "fn": 0})
            n_labels += 1
            located = [f for f in flags if hits(f, lab) or (lab.get("span") is None and f["category"] == lab["category"])]
            n_local += bool(located)
            ok = any(f["category"] == lab["category"] for f in located)
            n_cat_ok += ok
            row["tp" if ok else "fn"] += 1
    for k in keys:  # a flag matching no label of its category on a labelled item is a false positive
        for f in by_key[k]["flags"]:
            if not any(f["category"] == lab["category"] and hits(f, lab) for lab in labels[k]):
                per_cat.setdefault(f["category"], {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
    for row in per_cat.values():
        row["precision"] = _rate(row["tp"], row["tp"] + row["fp"])
        row["recall"] = _rate(row["tp"], row["tp"] + row["fn"])
        row["interval_recall"] = wilson(row["tp"], row["tp"] + row["fn"])

    return {
        "labelled_items": len(keys), "labelled_clean": len(clean),
        "matching_rule": f"item detected if it has any flag; a label is located by a flag whose span overlaps it on either side "
                         f"(or by category when the label has no span); primary span side: {span_side}",
        "overall": {
            "precision": _rate(len(flagged) - len(fa), len(flagged)),
            "recall": _rate(len(detected), len(with_err)), "interval_recall": wilson(len(detected), len(with_err)),
            "false_alarm_rate_clean": _rate(len(fa), len(clean)), "interval_false_alarm": wilson(len(fa), len(clean)),
            "localization": _rate(n_local, n_labels), "category_accuracy": _rate(n_cat_ok, n_local),
        },
        "per_category": dict(sorted(per_cat.items())),
        "per_check": thresholds,
        "label_sources": sources,
    }
