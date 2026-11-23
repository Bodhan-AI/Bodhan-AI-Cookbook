"""Load human labels for calibration and attach them to the items of the current run."""

import json
from dataclasses import replace
from pathlib import Path

from .dataset import _normalize_language, _parse_labels
from .models import Item


def load_calibration_items(
    path: Path,
    langs: set[str] | None,
    run_items: list[Item],
) -> tuple[list[Item], list[str]]:
    """Return run items carrying the labels from ``path``, plus human-readable warnings.

    The labels file may be the dataset itself or a labels-only file with ``id``, ``lang`` and
    ``labels``. Rows whose (id, lang) is not in the run are ignored and counted in one warning;
    an unusable row never aborts the run. A row without ``labels`` counts as a clean item, since
    it is part of the labelled subset.
    """
    normalized_langs = {_normalize_language(lang) for lang in langs} if langs is not None else None
    by_key = {(item.id, item.lang): item for item in run_items}
    items: dict[tuple[str, str], Item] = {}
    unmatched = 0
    skipped = 0
    warnings: list[str] = []
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(row, dict) or "id" not in row or "lang" not in row:
                skipped += 1
                continue
            lang = _normalize_language(row["lang"])
            if normalized_langs is not None and lang not in normalized_langs:
                continue
            key = (str(row["id"]), lang)
            run_item = by_key.get(key)
            if run_item is None:
                unmatched += 1
                continue
            text_length = max(len(run_item.source), len(run_item.translation or ""))
            labels = _parse_labels(row.get("labels", []), warnings, line_number, text_length)
            items[key] = replace(run_item, labels=labels)
    if unmatched:
        warnings.append(
            f"calibration: {unmatched} labelled item(s) are not in the run dataset and were ignored"
        )
    if skipped:
        warnings.append(f"calibration: skipped {skipped} unreadable label row(s)")
    return [items[key] for key in sorted(items)], warnings
