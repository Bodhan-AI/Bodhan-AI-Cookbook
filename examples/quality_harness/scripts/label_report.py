"""Each check against human labels (data/labels.jsonl from the review queue), at item level.

    python scripts/label_report.py data/labels.jsonl report-judge

For every check: of the reviewed items it raised something on, how many really have an error
(precision), and of the reviewed items with an error, how many it raised something on (recall).
`right kind` also requires one of its categories to match a labelled error's category.

ponytail: item level; the labels for errors the harness missed have no span, so span matching would
cover only part of them.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path


def main(labels_path, report):
    labels = {(e["id"], e["lang"]): e for e in map(json.loads, Path(labels_path).open(encoding="utf-8"))}
    recs = {(r["id"], r["lang"]): r for r in map(json.loads, (Path(report) / "items.jsonl").open(encoding="utf-8"))}
    reviewed = [k for k in labels if k in recs]
    erroneous = {k for k in reviewed if labels[k]["labels"]}
    print(f"{len(reviewed)} reviewed items, {len(erroneous)} with an error\n")
    stats = defaultdict(lambda: {"raised": 0, "on_error": 0, "right_kind": 0})
    for k in reviewed:
        types = {lab["type"] for lab in labels[k]["labels"]}
        by_check = defaultdict(set)
        for c in recs[k]["candidates"]:
            by_check[c["check"]].add(c["category"])
        by_check["(any check)"] = set().union(*by_check.values()) if by_check else set()
        if not recs[k]["candidates"]:
            del by_check["(any check)"]
        for check, cats in by_check.items():
            s = stats[check]
            s["raised"] += 1
            s["on_error"] += k in erroneous
            s["right_kind"] += bool(cats & types)
    print("| check | items raised on | precision | recall | right kind |\n|---|---|---|---|---|")
    for check, s in sorted(stats.items()):
        print(f"| {check} | {s['raised']} | {s['on_error']}/{s['raised']} | {s['on_error']}/{len(erroneous)} | {s['right_kind']}/{len(erroneous)} |")


if __name__ == "__main__":
    main(*sys.argv[1:3])
