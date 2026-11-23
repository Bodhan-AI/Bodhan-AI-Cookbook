"""Compare a run with a previous run (``--baseline``)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def _is_failed(check: dict) -> bool:
    return check.get("status") == "fail" or (
        "status" not in check and check.get("passed") is False
    )


def _is_review(check: dict) -> bool:
    return check.get("status") == "review"


def load_baseline(path: str | Path) -> dict:
    """Load a baseline ``summary.json`` (a run directory also works)."""
    path = Path(path)
    if path.is_dir():
        path = path / "summary.json"
    if not path.exists():
        raise FileNotFoundError(f"Baseline report not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _failures_by_item(report: dict) -> dict[str, set[str]]:
    return {
        item["id"]: {c["category"] for c in item.get("checks", []) if _is_failed(c)}
        for item in report.get("items", [])
    }


def compare_to_baseline(current: dict, baseline: dict) -> dict:
    """Item-level regressions and fixes, plus category deltas.

    A *regression* is an (item, category) pair that fails now but did not
    fail in the baseline; a *fix* is the reverse. Items present in only one
    run are listed separately and not counted as regressions.
    """
    now = _failures_by_item(current)
    before = _failures_by_item(baseline)

    shared = sorted(set(now) & set(before))
    regressions = [
        {"id": item_id, "category": category}
        for item_id in shared
        for category in sorted(now[item_id] - before[item_id])
    ]
    fixes = [
        {"id": item_id, "category": category}
        for item_id in shared
        for category in sorted(before[item_id] - now[item_id])
    ]

    now_counts = Counter(current.get("errors_by_category", {}))
    before_counts = Counter(baseline.get("errors_by_category", {}))
    categories = sorted(set(now_counts) | set(before_counts))

    def totals(report):
        return {
            key: report.get(key, 0)
            for key in ("total_items", "total_checks", "failed_checks", "review_checks", "passed_checks")
        }

    return {
        "totals": {"current": totals(current), "baseline": totals(baseline)},
        "category_delta": {
            category: {
                "baseline": before_counts.get(category, 0),
                "current": now_counts.get(category, 0),
                "delta": now_counts.get(category, 0) - before_counts.get(category, 0),
            }
            for category in categories
        },
        "regressions": regressions,
        "fixes": fixes,
        "new_items": sorted(set(now) - set(before)),
        "missing_items": sorted(set(before) - set(now)),
        "regression_count": len(regressions),
        "fix_count": len(fixes),
    }
