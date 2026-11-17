"""Review queue: export uncertain decisions as CSV, import human answers as calibration labels."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from ..config import Thresholds
from ..models import ItemResult

FIELDS = [
    "id",
    "lang",
    "category",
    "severity",
    "confidence",
    "threshold",
    "decision",
    "span",
    "source_span",
    "translation_excerpt",
    "source_excerpt",
    "evidence",
    "is_error",
    "correct_category",
    "correct_severity",
    "reviewer",
]


def export(results: list[ItemResult], th: Thresholds, margin: float = 0.2) -> str:
    rows = []
    for r in results:
        if r.status != "scored":
            continue
        for decision, pool in (("flagged", r.flags), ("not flagged", r.rejected)):
            for f in pool:
                thr = th.threshold(f.category)
                disputed = bool(f.judge and f.judge.startswith("rejected") and decision == "not flagged")
                if abs(f.confidence - thr) <= margin or disputed:
                    rows.append(
                        {
                            "id": r.id,
                            "lang": r.lang,
                            "category": f.category.value,
                            "severity": f.severity.value,
                            "confidence": f"{f.confidence:.3f}",
                            "threshold": f"{thr:.3f}",
                            "decision": decision,
                            "span": json.dumps(f.span),
                            "source_span": json.dumps(f.source_span),
                            "translation_excerpt": f.excerpt or "",
                            "source_excerpt": f.source_excerpt or "",
                            "evidence": " | ".join(f.evidence[:3]),
                            "is_error": "",
                            "correct_category": "",
                            "correct_severity": "",
                            "reviewer": "",
                        }
                    )
    rows.sort(key=lambda x: (abs(float(x["confidence"]) - float(x["threshold"])), x["id"], x["category"]))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def import_answers(csv_path: str | Path, dataset_path: str | Path, out_path: str | Path) -> int:
    """Turn reviewed CSV rows into a labels JSONL usable with --calibration. Returns the number of items written."""
    answers: dict[tuple[str, str], list[dict]] = {}
    reviewed: set[tuple[str, str]] = set()
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            verdict = (row.get("is_error") or "").strip().lower()
            if verdict not in {"yes", "no", "y", "n", "1", "0", "true", "false"}:
                continue
            key = (row["id"], row["lang"])
            reviewed.add(key)
            if verdict in {"yes", "y", "1", "true"}:
                span = json.loads(row["span"]) if row.get("span") not in (None, "", "null") else None
                source_span = json.loads(row["source_span"]) if row.get("source_span") not in (None, "", "null") else None
                label = {
                    "type": row.get("correct_category") or row["category"],
                    "severity": row.get("correct_severity") or row["severity"],
                    "span": span or source_span,
                    "field": "translation" if span else "source",
                    "annotator": f"human:{row.get('reviewer') or 'reviewer'}",
                }
                answers.setdefault(key, []).append(label)
    written = 0
    with open(dataset_path, encoding="utf-8") as src, open(out_path, "w", encoding="utf-8") as out:
        for line in src:
            if not line.strip():
                continue
            row = json.loads(line)
            key = (str(row.get("id")), str(row.get("lang")))
            if key not in reviewed:
                continue
            row["labels"] = answers.get(key, [])
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    return written
