"""Prepare blind human-review tasks and export explicitly reviewed calibration labels.

prepare --dataset data/demo.jsonl --out review.jsonl
export --review review.jsonl --out labels.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CATEGORIES = {"omission", "addition", "mistranslation", "number_unit", "term_entity", "format",
              "wrong_language_script", "speech_omission", "speech_repetition", "speech_garbled"}


def _write_new(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def prepare(dataset: Path, out: Path, per_group: int) -> int:
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    chosen = []
    for lang in sorted({row["lang"] for row in rows}):
        for injected in (False, True):
            group = [row for row in rows if row["lang"] == lang and bool(row.get("labels")) == injected]
            group.sort(key=lambda row: hashlib.sha256(str(row["id"]).encode()).hexdigest())
            chosen.extend(group[:per_group])
    # Do not expose the injector's label or group; a reviewer must assess the text independently.
    chosen.sort(key=lambda row: hashlib.sha256(("review:" + str(row["id"])).encode()).hexdigest())
    tasks = [{"id": row["id"], "lang": row["lang"], "source": row["source"],
              "translation": row.get("translation"), "reviewed": False, "annotator": "",
              "labels": None} for row in chosen]
    _write_new(out, tasks)
    return len(tasks)


def export(review: Path, out: Path) -> int:
    tasks = [json.loads(line) for line in review.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    seen = set()
    for task in tasks:
        if task.get("reviewed") is not True:
            continue
        item_id = str(task["id"])
        if item_id in seen:
            raise ValueError(f"duplicate reviewed id: {item_id}")
        seen.add(item_id)
        annotator = str(task.get("annotator") or "").strip()
        labels = task.get("labels")
        if not annotator or not isinstance(labels, list):
            raise ValueError(f"{item_id}: reviewed items require an annotator and labels list")
        out_labels = []
        for label in labels:
            if not isinstance(label, dict) or label.get("type") not in CATEGORIES:
                raise ValueError(f"{item_id}: unknown label category")
            if label.get("severity", "major") not in ("minor", "major", "critical"):
                raise ValueError(f"{item_id}: invalid severity")
            space = label.get("span_space", "target")
            if space not in ("source", "target"):
                raise ValueError(f"{item_id}: invalid span space")
            span = label.get("span")
            if span is not None:
                text = task.get("source" if space == "source" else "translation") or ""
                if (not isinstance(span, list) or len(span) != 2
                        or not all(isinstance(n, int) and not isinstance(n, bool) for n in span)
                        or not 0 <= span[0] < span[1] <= len(text)):
                    raise ValueError(f"{item_id}: invalid span")
            out_labels.append({**label, "span_space": space, "annotator": annotator})
        rows.append({"id": item_id, "annotator": annotator, "labels": out_labels})
    if not rows:
        raise ValueError("no items were explicitly reviewed")
    _write_new(out, rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--dataset", required=True, type=Path)
    prep.add_argument("--out", required=True, type=Path)
    prep.add_argument("--per-group", type=int, default=5)
    exp = commands.add_parser("export")
    exp.add_argument("--review", required=True, type=Path)
    exp.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        if args.per_group < 1:
            parser.error("--per-group must be positive")
        count = prepare(args.dataset, args.out, args.per_group)
    else:
        count = export(args.review, args.out)
    print(f"Wrote {count} records to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
