"""Baseline diff: compare this run's items.jsonl against a previous report's items.jsonl by id.

Reports new flags, resolved flags, severity changes, and per-category/per-language flag-count
deltas, with regressions listed first. --fail-on-regression uses `has_regression`.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .models import ItemResult


def _load_items_jsonl(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.is_file():
        raise FileNotFoundError(f"Baseline report is missing {path}")
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out[obj["id"]] = obj
    return out


def _flag_set(item: dict) -> set[tuple[str, str]]:
    return {(f["category"], f["severity"]) for f in item.get("flags", [])}


def compute_diff(baseline_dir: str, results: list[ItemResult]) -> dict:
    base = _load_items_jsonl(Path(baseline_dir) / "items.jsonl")
    cur = {r.id: r.to_json() for r in results}

    new_flags, resolved, sev_changes, incomparable = [], [], [], []
    cat_delta: Counter = Counter()
    lang_delta: Counter = Counter()

    for item_id in sorted(set(base) | set(cur)):
        b = base.get(item_id, {})
        c = cur.get(item_id, {})
        if not b or not c or b.get("status") == "skipped" or c.get("status") == "skipped":
            incomparable.append(item_id)
            continue
        bs, cs = _flag_set(b), _flag_set(c)
        bcats = {cat: sev for cat, sev in bs}
        ccats = {cat: sev for cat, sev in cs}
        for cat in sorted(ccats.keys() - bcats.keys()):
            sev = ccats[cat]
            new_flags.append({"id": item_id, "category": cat, "severity": sev})
            cat_delta[cat] += 1
            lang_delta[c.get("lang", "?")] += 1
        for cat in sorted(bcats.keys() - ccats.keys()):
            sev = bcats[cat]
            resolved.append({"id": item_id, "category": cat, "severity": sev})
            cat_delta[cat] -= 1
            lang_delta[b.get("lang", "?")] -= 1
        for cat in sorted(set(bcats) & set(ccats)):
            if bcats[cat] != ccats[cat]:
                sev_changes.append({"id": item_id, "category": cat,
                                    "from": bcats[cat], "to": ccats[cat]})

    return {
        "new_flags": new_flags,
        "resolved_flags": resolved,
        "severity_changes": sev_changes,
        "incomparable_items": incomparable,
        "category_delta": dict(sorted(cat_delta.items())),
        "language_delta": dict(sorted(lang_delta.items())),
        "regression_count": len(new_flags),
        "improvement_count": len(resolved),
    }


def has_regression(diff: dict, threshold: int = 1) -> bool:
    return diff.get("regression_count", 0) >= threshold


def write_diff_md(path: str, diff: dict) -> None:
    lines = ["# Baseline diff\n"]
    lines.append(f"- New flags (regressions): **{diff['regression_count']}**")
    lines.append(f"- Resolved flags (improvements): **{diff['improvement_count']}**")
    lines.append(f"- Severity changes: {len(diff['severity_changes'])}\n")
    lines.append(f"- Missing or skipped in either run: {len(diff.get('incomparable_items', []))}\n")

    lines.append("## Regressions first\n")
    if diff["new_flags"]:
        lines.append("| id | category | severity |")
        lines.append("| --- | --- | --- |")
        for f in diff["new_flags"][:50]:
            lines.append(f"| {f['id']} | {f['category']} | {f['severity']} |")
    else:
        lines.append("_No regressions._")
    lines.append("")

    lines.append("## Improvements\n")
    if diff["resolved_flags"]:
        lines.append("| id | category | severity |")
        lines.append("| --- | --- | --- |")
        for f in diff["resolved_flags"][:50]:
            lines.append(f"| {f['id']} | {f['category']} | {f['severity']} |")
    else:
        lines.append("_No resolved flags._")
    lines.append("")

    lines.append("## Per-category delta\n")
    lines.append("| category | delta |\n| --- | --- |")
    for cat, d in diff["category_delta"].items():
        lines.append(f"| {cat} | {d:+d} |")
    lines.append("\n## Severity changes\n")
    for change in diff["severity_changes"]:
        lines.append(f"- {change['id']} / {change['category']}: "
                     f"{change['from']} → {change['to']}")
    lines.append("\n## Incomparable items\n")
    lines.extend(f"- {item_id}" for item_id in diff.get("incomparable_items", []))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
