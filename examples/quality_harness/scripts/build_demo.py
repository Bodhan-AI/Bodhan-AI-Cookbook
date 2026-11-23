"""Build dataset files from data/sources.jsonl and data/in22.jsonl.

For now:
- one item per source and language, with no translation, for a `--generate` run;
- two items per IN22 sentence and language: `<id>-human` checks the IN22 human translation, and
  `<id>-bodhan` has Bodhan translate it with the human translation as the reference.
Injected errors and the calibration split come later (plan §19).
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--langs", default="hi,ta,bn")
    p.add_argument("--out", type=Path, default=ROOT / "data" / "starter.jsonl")
    args = p.parse_args()
    sources, in22 = read(ROOT / "data" / "sources.jsonl"), read(ROOT / "data" / "in22.jsonl")
    items = []
    for lang in args.langs.split(","):
        items += [{"id": s["id"], "source": s["source"], "lang": lang, "kind": s["kind"]} for s in sources]
        for s in in22:
            base = {"source": s["source"], "lang": lang, "kind": s["kind"], "domain": s["domain"]}
            items.append({"id": f"{s['id']}-human", **base, "translation": s["human"][lang], "translation_origin": "human"})
            items.append({"id": f"{s['id']}-bodhan", **base, "reference": s["human"][lang]})
    with args.out.open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    print(f"wrote {len(items)} items to {args.out}")


if __name__ == "__main__":
    main()
