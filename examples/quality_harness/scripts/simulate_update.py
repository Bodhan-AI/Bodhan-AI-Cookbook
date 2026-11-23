"""A simulated model update, to demonstrate --baseline: the same items, some translations swapped.

    python scripts/simulate_update.py data/injected.jsonl data/demo_baseline.jsonl data/demo_update.jsonl

From an injected dataset: the baseline is its base items; the update is the same items (same ids)
with --n of them replaced by one of their injected variants, spread over the categories, a few
harmless ones (hard negatives) included — those must not show up as regressions. `simulated_from`
records which variant each swapped item came from. Every text already went through a run of the
injected dataset, so with the cache both runs cost nothing.
"""

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("injected", type=Path)
    p.add_argument("baseline", type=Path)
    p.add_argument("update", type=Path)
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--clean", type=int, default=3, help="of the n, how many harmless (hard-negative) swaps")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    rows = [json.loads(line) for line in args.injected.read_text(encoding="utf-8").splitlines() if line.strip()]
    bases = [r for r in rows if "base" not in r]
    by_cat = defaultdict(list)
    for v in rows:
        if "base" in v:
            by_cat[v["injection"]["category"]].append(v)
    rng = random.Random(args.seed)
    errors = sorted(c for c in by_cat if c != "clean")
    picks, used = [], set()

    def take(cat):  # one variant of this category whose item isn't swapped yet
        pool = [v for v in by_cat[cat] if (v["base"], v["lang"]) not in used]
        if pool:
            v = rng.choice(pool)
            used.add((v["base"], v["lang"]))
            picks.append(v)

    for _ in range(args.clean):
        take("clean")
    for k in range(10 * args.n):  # then round-robin over the error categories
        if len(picks) >= args.n:
            break
        take(errors[k % len(errors)])
    swap = {(v["base"], v["lang"]): v for v in picks}
    with args.baseline.open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(b, ensure_ascii=False) + "\n" for b in bases)
    with args.update.open("w", encoding="utf-8") as f:
        for b in bases:
            v = swap.get((b["id"], b["lang"]))
            f.write(json.dumps({**b, "translation": v["translation"], "simulated_from": v["id"]} if v else b, ensure_ascii=False) + "\n")
    print(f"{len(bases)} items; {len(picks)} swapped: " + ", ".join(f"{v['id']}/{v['lang']}" for v in picks))


if __name__ == "__main__":
    main()
