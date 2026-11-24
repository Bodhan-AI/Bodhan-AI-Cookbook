"""python -m bodhan_eval.inject --dataset in.jsonl --out injected/ [--rate 0.5] [--retranslate]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from bodhan_eval.cache import Cache
from bodhan_eval.clients import BodhanClient
from bodhan_eval.cost import CostMeter
from bodhan_eval.dataset import load_dataset
from bodhan_eval.inject.run import inject_dataset, write_rows
from bodhan_eval.languages import load_languages, resolve_langs


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.inject", description=__doc__)
    p.add_argument("--dataset", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path, help="output directory")
    p.add_argument("--langs", default="")
    p.add_argument("--rate", type=float, default=0.5, help="share of items that get an error")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument(
        "--retranslate",
        action="store_true",
        help="also make mistranslation / term errors by re-translating perturbed English (paid)",
    )
    p.add_argument("--max-cost-inr", type=float, default=1.0)
    p.add_argument("--cache-dir", type=Path, default=Path(".bodhan_cache"))
    args = p.parse_args(argv)

    registry = load_languages()
    loaded = load_dataset(args.dataset, registry, resolve_langs(args.langs, registry))
    for prob in loaded.problems:
        print(f"line {prob.line_no}: {prob.reason}", file=sys.stderr)

    async def go():
        client = None
        if args.retranslate:
            client = BodhanClient(
                Cache(args.cache_dir / "cache.sqlite"), CostMeter(max_inr=args.max_cost_inr)
            )
        try:
            return await inject_dataset(
                loaded.items, registry, args.out, rate=args.rate, seed=args.seed, client=client
            ), client
        finally:
            if client is not None:
                await client.aclose()

    (rows, stats), client = asyncio.run(go())
    write_rows(rows, args.out / "injected.jsonl")
    if client is not None:
        stats["cost"] = client.cost.summary()
    (args.out / "injection_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats["counts"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
