"""Pick IN22-Gen sentences for the demo dataset and write data/in22.jsonl.

IN22-Gen (AI4Bharat, CC BY 4.0): English sentences with professional human translations.
https://huggingface.co/datasets/ai4bharat/IN22-Gen. Its data files are gated, so download
IN22_benchmark.tar.gz once into data/raw/in22/ and extract it there (data/raw/ is not committed).

Selection is seeded, not hand-picked: one sentence per domain (13), plus one short (6-10 words)
and one long (46+ words) sentence, so the lengths are not all mid-range.
"""

import argparse
import csv
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = {"hi": "hin_Deva", "ta": "tam_Taml", "bn": "ben_Beng"}
SEED = 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--raw", type=Path, default=ROOT / "data" / "raw" / "in22" / "IN22_benchmark")
    p.add_argument("--out", type=Path, default=ROOT / "data" / "in22.jsonl")
    args = p.parse_args()

    def lines(code):
        return (args.raw / "gen" / f"test.{code}").read_text(encoding="utf-8").splitlines()

    meta = list(csv.DictReader((args.raw / "metadata_gen.tsv").open(encoding="utf-8"), delimiter="\t"))
    english = lines("eng_Latn")
    human = {lang: lines(code) for lang, code in LANGS.items()}
    assert len(meta) == len(english) and all(len(v) == len(english) for v in human.values()), "files are not aligned"

    rng = random.Random(SEED)
    picked = [rng.choice([i for i, m in enumerate(meta) if m["domain"] == d]) for d in sorted({m["domain"] for m in meta})]
    for buckets in ({"6 - 10"}, {"46 - 60", "61 - 80"}):
        picked.append(rng.choice([i for i, m in enumerate(meta) if m["bucket"] in buckets and i not in picked]))

    with args.out.open("w", encoding="utf-8") as f:
        for i in sorted(picked):
            m = meta[i]
            row = {
                "id": f"in22-{i + 1:04d}", "kind": "in22", "domain": m["domain"], "source": english[i],
                "human": {lang: v[i] for lang, v in human.items()},
                "attribution": {"source": "IN22-Gen (AI4Bharat), https://huggingface.co/datasets/ai4bharat/IN22-Gen",
                                "line": i + 1, "license": "CC BY 4.0", "origin": m["source"], "url": m["url"] or None},
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(picked)} sentences to {args.out}")


if __name__ == "__main__":
    main()
