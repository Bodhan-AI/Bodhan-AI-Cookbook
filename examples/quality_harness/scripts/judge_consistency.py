"""Judge consistency (brief: re-judging an item gives the same verdict >= 90% of the time).

    python scripts/judge_consistency.py report-judge --items 30 --repeats 3

Re-judges a seeded sample of a report's items (spread across languages) `repeats - 1` more times;
the report's own judgement is the first. Repeats bypass the cache (Clients.llm `repeat`), so this
costs LLM calls but no Bodhan calls. Writes <report>/consistency.json (summary.md shows it); over all
pairs of runs of the same item:
- verdict agreement: both runs say "has an error", or both say "no error";
- category agreement: both runs report the same set of categories.
"""

import argparse
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from bodhan_eval.clients import Clients  # noqa: E402
from bodhan_eval.config import Config, env_settings, load_dotenv  # noqa: E402
from bodhan_eval.judge import judge  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("report", type=Path)
    p.add_argument("--items", type=int, default=30)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    load_dotenv()
    clients = Clients(Config(dataset=Path("d"), langs=(), out=Path("o"), **env_settings(os.environ)), Path(".bodhan_cache"))

    recs = [r for r in map(json.loads, (args.report / "items.jsonl").open(encoding="utf-8")) if r["checks"].get("judge") == "ok"]
    by_lang = {}
    for r in recs:
        by_lang.setdefault(r["lang"], []).append(r)
    rng = random.Random(args.seed)
    per = -(-args.items // len(by_lang))
    sample = [r for lang in sorted(by_lang) for r in rng.sample(by_lang[lang], min(per, len(by_lang[lang])))][:args.items]

    def runs(r):
        first = {c["category"] for c in r["candidates"] if c["check"] == "judge"}
        out = [first]
        for k in range(1, args.repeats):
            cands, _ = judge(r["source"], r["translation"], r["back_translation"], r["lang"], clients, repeat=k)
            out.append({c.category for c in cands})
        return out

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(runs, sample))
    clients.close()

    pairs = [(a, b) for res in results for a, b in combinations(res, 2)]
    verdict = sum(bool(a) == bool(b) for a, b in pairs) / len(pairs)
    category = sum(a == b for a, b in pairs) / len(pairs)
    all_same = sum(len({bool(x) for x in res}) == 1 for res in results)
    counts = ", ".join(f"{k}: {sum(r['lang'] == k for r in sample)}" for k in sorted(by_lang))
    print(f"{len(sample)} items x {args.repeats} runs ({counts})")
    print(f"verdict agreement (error / no error), pairwise: {verdict:.0%}; items with the same verdict in every run: {all_same}/{len(sample)}")
    print(f"category-set agreement, pairwise: {category:.0%}")
    (args.report / "consistency.json").write_text(json.dumps({
        "items": len(sample), "runs": args.repeats, "verdict_agreement": round(verdict, 3),
        "same_verdict_every_run": all_same, "category_agreement": round(category, 3)}, indent=2) + "\n", encoding="utf-8")
    for r, res in zip(sample, results):
        if len({frozenset(x) for x in res}) > 1:
            print(f"  differs: {r['id']} {r['lang']}: {[sorted(x) for x in res]}")


if __name__ == "__main__":
    main()
