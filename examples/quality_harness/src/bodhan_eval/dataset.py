"""Stage 0: load and validate a JSONL dataset. A bad or partial line never stops the run --
it is logged, skipped and counted with a reason.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .models import Item


@dataclass
class LoadReport:
    items: list[Item] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {line, id?, reason}
    duplicate_ids: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        return {"loaded": len(self.items), "skipped": len(self.skipped),
                "duplicate_ids": len(self.duplicate_ids)}


def load_dataset(path: str | Path, langs: Iterable[str] | None = None) -> LoadReport:
    report = LoadReport()
    seen: set[str] = set()
    lang_filter = set(langs) if langs else None
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                report.skipped.append({"line": lineno, "reason": f"json_error: {exc}"})
                continue
            if not isinstance(obj, dict):
                report.skipped.append({"line": lineno, "reason": "expected_json_object"})
                continue
            missing = [k for k in ("id", "source", "lang") if k not in obj or obj.get(k) in (None, "")]
            if missing:
                report.skipped.append({"line": lineno, "id": obj.get("id"),
                                       "reason": f"missing_required: {','.join(missing)}"})
                continue
            if (not isinstance(obj["source"], str) or not isinstance(obj["lang"], str)
                    or not isinstance(obj["id"], (str, int))
                    or any(obj.get(k) is not None and not isinstance(obj[k], str)
                           for k in ("translation", "reference", "audio"))):
                report.skipped.append({"line": lineno, "reason": "invalid_field_type"})
                continue
            item_id = str(obj["id"])
            if item_id in seen:
                report.duplicate_ids.append(item_id)
                report.skipped.append({"line": lineno, "id": item_id, "reason": "duplicate_id"})
                continue
            if lang_filter is not None and obj["lang"] not in lang_filter:
                report.skipped.append({"line": lineno, "id": item_id,
                                       "reason": f"lang_not_selected: {obj['lang']}"})
                continue
            try:
                item = Item.from_dict(obj)
            except Exception as exc:  # noqa: BLE001
                report.skipped.append({"line": lineno, "id": item_id, "reason": f"parse: {exc}"})
                continue
            seen.add(item_id)
            report.items.append(item)
    return report
