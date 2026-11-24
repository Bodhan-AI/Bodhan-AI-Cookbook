"""Import completed review queue CSV rows as attributable human annotations."""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from .judge import CATEGORIES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    grouped, annotators = defaultdict(list), defaultdict(set)
    with Path(args.csv).open(encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            decision = (row.get("human_type") or "").strip()
            if not decision:
                continue
            annotator = (row.get("annotator") or "").strip()
            if not annotator:
                raise ValueError(f"row {line}: completed review needs an annotator")
            key = (row["id"], row["lang"])
            annotators[key].add(annotator)
            if decision == "clean":
                grouped[key]  # Preserve an explicit empty annotation.
                continue
            if decision not in CATEGORIES or row.get("human_severity") not in ("minor", "major", "critical"):
                raise ValueError(f"row {line}: invalid human category or severity")
            span = json.loads(row["span"])
            if not isinstance(span, list) or len(span) != 2 or any(type(v) is not int for v in span) or not 0 <= span[0] < span[1]:
                raise ValueError(f"row {line}: invalid span")
            grouped[key].append({"type": decision, "severity": row["human_severity"], "span": span, "span_field": row["span_field"]})
    output = [
        {"id": key[0], "lang": key[1], "labels": labels, "label_origin": "human:" + ",".join(sorted(annotators[key]))}
        for key, labels in sorted(grouped.items())
    ]
    Path(args.out).write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in output), encoding="utf-8")
    print(f"Imported {len(output)} annotated items")


if __name__ == "__main__":
    main()
