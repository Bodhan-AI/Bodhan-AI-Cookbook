"""Build the demo dataset: translate the English sources with Indic-Translate, then inject
labelled errors programmatically. The result (data/demo.jsonl) has >=100 items, ~50% clean,
with injected errors and injector labels. Audio is NOT produced here (generate it at run time
with `--generate`); only text is written.

    python scripts/build_demo.py                 # real Bodhan (needs keys in .env)
    python scripts/build_demo.py --fake          # offline dry-run (unrealistic translations)

Indian-language text is produced ONLY by Indic-Translate; the injector perturbs it
programmatically. No LLM writes Indic text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# allow running from a clean checkout without `pip install` (works on any Python >=3.9)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bodhan_eval.cache import Cache
from bodhan_eval.clients.bodhan import build_bodhan_clients
from bodhan_eval.clients.fake import build_fake_clients
from bodhan_eval.concurrency import ConcurrencyLimiter
from bodhan_eval.config import load_api_config, load_languages
from bodhan_eval.cost import CostLedger
from bodhan_eval.dataset import load_dataset
from bodhan_eval.inject import build_injected
from bodhan_eval.logging_utils import StructuredLogger

ROOT = Path(__file__).resolve().parent.parent


async def _translate_all(clients, items, concurrency: int):
    sem = asyncio.Semaphore(concurrency)
    out = {}

    async def one(it):
        async with sem:
            r = await clients.translate.translate(it.source, "en", it.lang)
            out[it.id] = r.text

    await asyncio.gather(*(one(it) for it in items))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sources", default=str(ROOT / "data" / "sources_en.jsonl"))
    p.add_argument("--out", default=str(ROOT / "data" / "demo.jsonl"))
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--clean-fraction", type=float, default=0.5)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--cache-dir", default=str(ROOT / ".cache"))
    p.add_argument("--max-cost-inr", type=float, default=None,
                   help="limit estimated Bodhan spend for this build")
    p.add_argument("--fake", action="store_true")
    args = p.parse_args(argv)

    if args.fake and Path(args.out).resolve() == (ROOT / "data" / "demo.jsonl").resolve():
        p.error("--fake requires a separate --out path; it must not overwrite the submission dataset")

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

    rep = load_dataset(args.sources)
    cache = Cache(Path(args.cache_dir))
    ledger = CostLedger(max_inr=args.max_cost_inr)
    logger = StructuredLogger(None, level="info")
    if args.fake:
        clients = build_fake_clients(cache, ledger, {})
    else:
        limiter = ConcurrencyLimiter(args.concurrency)
        clients = build_bodhan_clients(load_api_config(), cache, ledger, limiter, logger,
                                       dict(os.environ))

    translations = asyncio.run(_translate_all(clients, rep.items, args.concurrency))
    if not args.fake:
        languages = load_languages()
        invalid = [it.id for it in rep.items
                   if not languages.script_for(it.lang) or not translations.get(it.id, "").strip()
                   or not any(languages.script_for(it.lang).contains(ord(ch))
                              for ch in translations[it.id])]
        if invalid:
            raise ValueError(f"Translations missing expected script for {len(invalid)} items; "
                             "output was not written")
    clean = []
    for it in rep.items:
        clean.append({"id": it.id, "source": it.source, "lang": it.lang,
                      "translation": translations.get(it.id, "")})

    injected = build_injected(clean, seed=args.seed, clean_fraction=args.clean_fraction)
    with Path(args.out).open("w", encoding="utf-8") as fh:
        for line in injected:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    labelled = sum(1 for x in injected if x.get("label_source") == "injector")
    print(f"Wrote {len(injected)} items ({labelled} injected, {len(injected) - labelled} clean) "
          f"to {args.out}. Actual spend: {ledger.summary()['total_inr']} INR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
