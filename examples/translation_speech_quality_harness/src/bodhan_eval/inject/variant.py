"""Make a 'second system version' of a dataset (same ids) to demonstrate --baseline diffs.

    python -m bodhan_eval.inject.variant --in data/demo.jsonl --out data/demo_v2.jsonl --seed 11

About 15% of injected translation errors are 'fixed' (the clean translation of the same source is restored) and
about 10% of clean items get a new programmatic error, so the diff has both regressions and improvements.
"""
from __future__ import annotations

import argparse
import json
import random

from . import text_injector as ti


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=11)
    a = p.parse_args()
    rng = random.Random(a.seed)
    rows = [json.loads(line) for line in open(a.inp, encoding="utf-8") if line.strip()]
    clean = {(r["group"], r["lang"]): r["translation"] for r in rows if r.get("labels") == [] and r.get("translation")}
    out, fixed, broken = [], 0, 0
    for r in rows:
        r = dict(r)
        lab = (r.get("labels") or [None])[0]
        if lab and not lab.get("area") and rng.random() < 0.15 and (r["group"], r["lang"]) in clean:
            r["translation"], r["labels"] = clean[(r["group"], r["lang"])], []
            r["variant_note"] = "fixed in v2"
            fixed += 1
        elif r.get("labels") == [] and not r.get("audio") and rng.random() < 0.10:
            res = ti.number(r["translation"], r["source"], rng) or ti.omission(r["translation"], r["source"], rng)
            if res:
                r["translation"], lab2 = res
                r["labels"] = [lab2]
                r["variant_note"] = "regressed in v2"
                broken += 1
        out.append(r)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(out)} items: {fixed} fixed, {broken} regressed")


if __name__ == "__main__":
    main()
