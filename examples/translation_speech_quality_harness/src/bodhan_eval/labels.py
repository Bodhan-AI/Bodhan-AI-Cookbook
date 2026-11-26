"""Human labelling helper.

  python -m bodhan_eval.labels sheet  --dataset data/demo.jsonl --out labeling_sheet.csv --n 20
  python -m bodhan_eval.labels import --sheet labeling_sheet.csv --out labels_human.jsonl --annotator <your-handle>

The sheet shows only source + translation (NOT the injected labels), so your judgement is blind.
Fill `error_types` with ';'-separated categories, leave blank if the translation is clean.
Then use the result with --calibration labels_human.jsonl. Imported labels carry label_source "human:<annotator>".
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json

from .calibrate import ALIASES

CATEGORIES = ["omission", "addition", "mistranslation", "number", "term", "format", "wrong_language"]


def make_sheet(dataset: str, out: str, n: int, seed: int = 0) -> int:
    rows = [json.loads(line) for line in open(dataset, encoding="utf-8") if line.strip()]
    rows = [r for r in rows if r.get("translation")]
    rows.sort(key=lambda r: hashlib.sha1(f"{seed}:{r['id']}".encode()).hexdigest())
    with open(out, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig so Excel shows Indic text
        w = csv.writer(f)
        w.writerow(["id", "lang", "source", "translation", "error_types (blank = clean; use: " + ";".join(CATEGORIES) + ")", "notes"])
        for r in rows[:n]:
            w.writerow([r["id"], r["lang"], r["source"], r["translation"], "", ""])
    return min(n, len(rows))


def import_sheet(sheet: str, out: str, annotator: str, kind: str = "human") -> tuple[int, list[str]]:
    problems, n = [], 0
    with open(sheet, encoding="utf-8-sig", newline="") as f, open(out, "w", encoding="utf-8") as o:
        for row in csv.reader(f):
            if not row or row[0] == "id":
                continue
            iid, raw = row[0], (row[4] if len(row) > 4 else "").strip()
            labels = []
            for part in [p.strip().lower().replace(" ", "_") for p in raw.split(";") if p.strip()]:
                cat = ALIASES.get(part, part)
                if cat not in CATEGORIES:
                    problems.append(f"{iid}: unknown error type '{part}'")
                    continue
                labels.append({"type": cat, "severity": "major", "span": None, "label_source": f"{kind}:{annotator}"})
            o.write(json.dumps({"id": iid, "labels": labels}, ensure_ascii=False) + "\n")
            n += 1
    return n, problems


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bodhan_eval.labels")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet")
    s.add_argument("--dataset", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--seed", type=int, default=0)
    i = sub.add_parser("import")
    i.add_argument("--sheet", required=True)
    i.add_argument("--out", required=True)
    i.add_argument("--annotator", required=True)
    i.add_argument("--kind", choices=["human", "ai"], default="human", help="who made the labels; recorded in label_source")
    a = p.parse_args(argv)
    if a.cmd == "sheet":
        print(f"wrote {make_sheet(a.dataset, a.out, a.n, a.seed)} items to {a.out}")
        return 0
    n, problems = import_sheet(a.sheet, a.out, a.annotator, a.kind)
    print(f"imported {n} items to {a.out}")
    for pr in problems:
        print("WARNING:", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
