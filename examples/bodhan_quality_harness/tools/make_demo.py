"""Build the demo dataset with Bodhan models.

The committed ``data/demo.jsonl`` is a development fixture: its translations were
produced offline with a non-Bodhan MT system purely so the checks and tests could
be developed and measured without API access. Before submitting, regenerate it
with this script so every Indian-language string in the dataset comes from
Indic-Translate, as the brief requires.

    python tools/make_demo.py --out data/demo.jsonl --calibration data/calibration.jsonl

Reads ``data/sources.jsonl`` (English source text), translates each line into
Hindi, Tamil and Bengali with Indic-Translate, keeps half the items clean and
injects one labelled error into the other half.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bodhan_eval import config                       # noqa: E402
from bodhan_eval.bodhan import BodhanClient, CostTracker  # noqa: E402
from bodhan_eval.cache import Cache                   # noqa: E402
from bodhan_eval.inject import inject_item            # noqa: E402

LANGS = ["hi", "ta", "bn"]
KINDS = ["number", "omission", "addition", "term", "format", "language", "repetition"]


async def build(out: Path, calibration: Path, clean_fraction: float, seed: int) -> None:
    config.load_env()
    if not config.have_translate():
        raise SystemExit("BODHAN_TRANSLATE_KEY is not set — cannot generate demo data")

    sources = [json.loads(l) for l in
               (Path(__file__).resolve().parents[1] / "data" / "sources.jsonl")
               .read_text(encoding="utf-8").splitlines() if l.strip()]

    cache = Cache(Path(".cache/bodhan_eval"))
    costs = CostTracker()
    rng = random.Random(seed)

    rows: list[dict] = []
    labelled: list[dict] = []

    async with BodhanClient(cache, costs, concurrency=1, max_retries=6) as client:
        for src in sources:
            for lang in LANGS:
                translation = await client.translate(src["source"], lang)
                await asyncio.sleep(0.3)
                item_id = f"{src['id']}-{lang}"
                if rng.random() < clean_fraction:
                    rows.append({"id": item_id, "source": src["source"],
                                 "lang": lang, "translation": translation, "labels": []})
                    continue
                variants = inject_item(item_id, src["source"], translation, lang,
                                       seed=seed, kinds=KINDS)
                if not variants:
                    rows.append({"id": item_id, "source": src["source"],
                                 "lang": lang, "translation": translation, "labels": []})
                    continue
                v = rng.choice(variants)
                row = {"id": v["id"], "source": src["source"], "lang": lang,
                       "translation": v["translation"], "labels": v["labels"]}
                rows.append(row)
                labelled.append(row)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    calibration.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False)
                  for r in labelled[: max(1, len(labelled) // 3)]) + "\n",
        encoding="utf-8")

    print(f"wrote {len(rows)} items to {out}")
    print(f"wrote {min(len(labelled), max(1, len(labelled) // 3))} labelled items to {calibration}")
    print(f"cost: Rs {costs.spent:.2f}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("data/demo.jsonl"))
    p.add_argument("--calibration", type=Path, default=Path("data/calibration.jsonl"))
    p.add_argument("--clean-fraction", type=float, default=0.5)
    p.add_argument("--seed", type=int, default=11)
    a = p.parse_args()
    asyncio.run(build(a.out, a.calibration, a.clean_fraction, a.seed))


if __name__ == "__main__":
    main()
