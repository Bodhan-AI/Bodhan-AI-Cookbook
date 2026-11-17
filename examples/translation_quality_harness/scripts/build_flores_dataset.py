"""Build the demo, calibration and held-out datasets from FLORES-101 (CC BY-SA 4.0).

FLORES gives professional human translations of the same English sentences in every
language. They stand in for system outputs, so the harness can be measured end to end: the
clean items are human translations, and errors are injected programmatically.

    python scripts/build_flores_dataset.py --flores-dir path/to/flores101_dataset --out data

Download: https://dl.fbaipublicfiles.com/flores101/dataset/flores101_dataset.tar.gz
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

import httpx
import regex

from bodhan_eval.cache import Cache
from bodhan_eval.clients.llm import LLMClient
from bodhan_eval.config import Settings, load_dotenv_if_present
from bodhan_eval.inject.injector import align_terms, inject_text

CODES = {"hi": "hin", "ta": "tam", "bn": "ben", "mr": "mar", "te": "tel", "ur": "urd", "gu": "guj", "as": "asm"}
ALTERNATES = {"hi": ["mr", "bn", "ta"], "ta": ["te", "hi", "bn"], "bn": ["as", "hi", "ta"], "mr": ["hi", "te"], "te": ["ta", "mr"]}
FORMULAS = [
    "E = mc^2",
    "a^2 + b^2 = c^2",
    "v = u + at",
    "F = ma",
    "\\frac{d}{t} = v",
    "\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}",
    "A = \\pi r^2",
    "x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}",
    "PV = nRT",
    "\\int_0^1 x^2 \\, dx = \\frac{1}{3}",
]


def read_split(root: Path, split: str, langs: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    texts = {"en": (root / split / f"eng.{split}").read_text(encoding="utf-8").splitlines()}
    for lang in langs:
        texts[lang] = (root / split / f"{CODES[lang]}.{split}").read_text(encoding="utf-8").splitlines()
    meta = (root / f"metedata_{split}.tsv").read_text(encoding="utf-8").splitlines()[1:]
    urls = [line.split("\t")[0] for line in meta]
    return texts, urls


def compose(kind: str, idx: list[int], lang: str, texts: dict[str, list[str]], rng: random.Random) -> tuple[str, str, dict]:
    def side(code: str) -> str:
        s = [texts[code][i] for i in idx]
        if kind == "sentence":
            return s[0]
        if kind == "paragraph":
            return " ".join(s)
        if kind == "list":
            return "\n".join(f"- {x}" for x in s)
        if kind == "numbered":
            return "\n".join(f"{k + 1}. {x}" for k, x in enumerate(s))
        if kind == "table":
            rows = "\n".join(f"| {k + 1} | {x} |" for k, x in enumerate(s))
            return f"| # | ¶ |\n|---|---|\n{rows}"
        if kind == "heading":
            return f"## {s[0]}\n\n" + " ".join(s[1:])
        raise ValueError(kind)

    alternates = {code: side(code) for code in ALTERNATES.get(lang, []) if code in texts}
    return side("en"), side(lang), {"alternates": alternates}


def with_formula(source: str, translation: str, alternates: dict[str, str], rng: random.Random) -> tuple[str, str, dict[str, str]]:
    formula = rng.choice(FORMULAS)
    if rng.random() < 0.5:
        tail = f"\n\n$${formula}$$"
    else:
        tail = f" (${formula}$)"
    return source + tail, translation + tail, {k: v + tail for k, v in alternates.items()}


def build(texts: dict[str, list[str]], urls: list[str], lang: str, n: int, offset: int, seed: int, split: str) -> list[dict]:
    rng = random.Random(f"{seed}:{lang}:{split}")
    total = len(texts["en"])
    order = list(range(total))
    random.Random(f"{seed}:{split}:order").shuffle(order)
    order = order[offset:] + order[:offset]
    plan = ["sentence"] * 15 + ["paragraph"] * 9 + ["list"] * 3 + ["numbered"] * 2 + ["table"] * 3 + ["heading"] * 2 + ["latex"] * 6
    plan = (plan * ((n // len(plan)) + 1))[: max(n, len(plan))]
    rng.shuffle(plan)
    plan = plan[:n]
    rows, used = [], set()
    cursor = 0
    digit_bias = [i for i in order if regex.search(r"\d", texts["en"][i])]
    for k, kind in enumerate(plan):
        want = {"sentence": 1, "paragraph": rng.choice([2, 3]), "list": 3, "numbered": 3, "table": rng.choice([2, 3]), "heading": 3, "latex": 1}[kind]
        pool = digit_bias if kind == "sentence" and k % 2 == 0 else order
        idx = None
        for start in pool[cursor:] + pool[:cursor]:
            if start in used:
                continue
            group = [start + j for j in range(want)]
            if group[-1] >= total or any(g in used for g in group) or len({urls[g] for g in group}) != 1:
                continue
            if any(len(texts["en"][g]) > 260 for g in group):
                continue
            idx = group
            break
        if idx is None:
            continue
        cursor = (cursor + 7) % max(len(order), 1)
        used.update(idx)
        source, translation, extra = compose("sentence" if kind == "latex" else kind, idx, lang, texts, rng)
        alternates = extra["alternates"]
        if kind == "latex":
            source, translation, alternates = with_formula(source, translation, alternates, rng)
        rows.append(
            {
                "id": f"fl-{split}-{idx[0]:04d}-{kind}",
                "source": source,
                "lang": lang,
                "translation": translation,
                "alternates": alternates,
                "meta": {
                    "provenance": f"FLORES-101 {split} lines {idx[0] + 1}-{idx[-1] + 1} ({urls[idx[0]]})",
                    "license": "CC BY-SA 4.0",
                    "content": kind,
                    "translation_origin": "FLORES-101 human translation",
                },
            }
        )
    return rows


async def term_spans(rows: list[dict], cache_dir: str) -> dict:
    settings = Settings.from_env()
    if not settings.llm_configured:
        return {}
    cache = Cache(cache_dir)
    async with httpx.AsyncClient() as http:
        llm = LLMClient(settings, cache, http)
        return await align_terms(rows, llm)


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n" for r in rows), encoding="utf-8")
    labelled = sum(1 for r in rows if r.get("labels"))
    print(f"{path}: {len(rows)} items, {labelled} with injected errors")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--flores-dir", required=True)
    p.add_argument("--out", default="data")
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--per-lang", type=int, default=40)
    p.add_argument("--cache-dir", default=".bodhan_cache")
    args = p.parse_args()
    load_dotenv_if_present()
    root, out = Path(args.flores_dir), Path(args.out)

    langs = ["hi", "ta", "bn", "mr", "te", "as"]
    dev, dev_urls = read_split(root, "dev", langs)
    devtest, devtest_urls = read_split(root, "devtest", langs)

    demo = [r for k, lang in enumerate(["hi", "ta", "bn"]) for r in build(dev, dev_urls, lang, args.per_lang, k * 211, args.seed, "dev")]
    calibration = [r for k, lang in enumerate(["hi", "ta", "bn"]) for r in build(devtest, devtest_urls, lang, 30, k * 157, args.seed + 1, "devtest")]
    heldout = [r for k, lang in enumerate(["mr", "te"]) for r in build(devtest, devtest_urls, lang, 30, 500 + k * 131, args.seed + 2, "devtest")]

    for name, rows, seed in (("demo", demo, args.seed), ("calibration", calibration, args.seed + 1), ("heldout_mr_te", heldout, args.seed + 2)):
        spans = asyncio.run(term_spans(rows, args.cache_dir))
        injected = inject_text(rows, rate=0.5, seed=seed, term_spans=spans)
        write(out / f"{name}.jsonl", injected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
