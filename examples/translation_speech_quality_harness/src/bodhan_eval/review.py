"""Turn a filled-in review_queue.csv into a labels JSONL usable with --calibration.

    python -m bodhan_eval.review --csv report/review_queue.csv --annotator yourname --out data/labels_review.jsonl

Rows answered "yes" become labels (category from `correct_category` or the flagged category, span from
the harness flag); items whose every reviewed row is "no" become verified-clean (labels == []).
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def convert(csv_path: Path, items_jsonl: Path, annotator: str) -> list[dict]:
    flags = {}
    with open(items_jsonl, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            flags[r["id"]] = r
    answers: dict[str, list[dict]] = defaultdict(list)
    with open(csv_path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ans = (row.get("is_real_error (yes/no)") or "").strip().lower()
            if ans in ("yes", "no"):
                answers[row["id"]].append({**row, "_ans": ans})
    out = []
    for item_id, rows in sorted(answers.items()):
        labels = []
        for row in rows:
            if row["_ans"] != "yes":
                continue
            cat = (row.get("correct_category") or row["category"]).strip()
            f = next((f for f in flags.get(item_id, {}).get("flags", []) if f["category"] == row["category"]), None)
            lab = {"type": cat, "severity": (f or {}).get("severity", "major"), "annotator": row.get("annotator") or annotator}
            if f and f.get("span"):
                lab["span"] = f["span"]
            if f and f.get("source_span"):
                lab["source_span"] = f["source_span"]
            labels.append(lab)
        out.append({"id": item_id, "labels": labels})
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True, type=Path)
    p.add_argument("--items", type=Path, help="items.jsonl of the same report (default: next to the CSV)")
    p.add_argument("--annotator", required=True)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    rows = convert(a.csv, a.items or a.csv.parent / "items.jsonl", a.annotator)
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} reviewed items to {a.out}")


if __name__ == "__main__":
    main()
