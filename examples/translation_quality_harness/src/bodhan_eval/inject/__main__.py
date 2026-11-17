"""python -m bodhan_eval.inject --input clean.jsonl --output injected.jsonl [--rate 0.5] [--seed 13] [--align-terms]"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from ..cache import Cache, atomic_write_text
from ..clients.llm import LLMClient
from ..config import Settings, load_dotenv_if_present
from .injector import align_terms, inject_text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.inject", description="Seed labelled translation errors into clean items.")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--rate", type=float, default=0.5, help="share of items that receive one error (default 0.5)")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--categories", default=None, help="comma-separated subset of translation categories")
    p.add_argument("--align-terms", action="store_true", help="use the LLM to locate rendered names (indices only) for term errors")
    p.add_argument("--cache-dir", default=".bodhan_cache")
    args = p.parse_args(argv)
    load_dotenv_if_present()
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line.strip()]
    spans = asyncio.run(_align(rows, args.cache_dir)) if args.align_terms else None
    cats = [c.strip() for c in args.categories.split(",")] if args.categories else None
    out = inject_text(rows, rate=args.rate, seed=args.seed, categories=cats, term_spans=spans)
    atomic_write_text(Path(args.output), "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
    n = sum(1 for r in out if r.get("labels"))
    print(f"wrote {len(out)} items ({n} with an injected error) to {args.output}")
    return 0


async def _align(rows: list[dict], cache_dir: str):
    settings = Settings.from_env()
    cache = Cache(cache_dir)
    async with httpx.AsyncClient() as http:
        llm = LLMClient(settings, cache, http)
        if not llm.available:
            return None
        return await align_terms(rows, llm)


if __name__ == "__main__":
    raise SystemExit(main())
