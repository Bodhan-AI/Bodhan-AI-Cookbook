"""Build a test set in languages the demo does not use, to check that nothing is tuned to Hindi, Tamil or Bengali.

python tools/build_unseen.py --langs ur,mr --out data_unseen --env-file ../../../.env

Takes 20 demo sources (by SHA-256 of the id), translates them with Indic-Translate, speaks up to 8 short plain items
per language with Indic-Speak, and injects errors into half the rows with the same injector as the demo (seed 7).
Writes <out>/unseen.jsonl and <out>/audio/. Costs about INR 2 for two languages; every call is cached.
"""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))

from bodhan_eval import inject  # noqa: E402
from bodhan_eval.pipeline import parse_langs  # noqa: E402
from bodhan_eval.__main__ import load_env_file  # noqa: E402
from bodhan_eval.cache import Cache  # noqa: E402
from bodhan_eval.clients import KEY_ENV, Bodhan, Budget, RunLog  # noqa: E402
from bodhan_eval.generate import speak_long  # noqa: E402

sys.path.insert(0, str(HERE / "tools"))
from build_demo import plain, sha  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--langs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--env-file")
    p.add_argument("--sources", type=int, default=20)
    p.add_argument("--audio-per-lang", type=int, default=8)
    p.add_argument("--max-cost-inr", type=float, default=4.0)
    a = p.parse_args()
    if a.env_file:
        load_env_file(a.env_file)
    langs = sorted(parse_langs(a.langs)[0])
    out = Path(a.out)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    demo = {}
    for r in map(json.loads, (HERE / "data" / "demo_clean.jsonl").read_text(encoding="utf-8").splitlines()):
        demo.setdefault(r["id"], r["source"])
    ids = sorted(demo, key=lambda i: sha(f"unseen/{i}"))[:a.sources]
    bodhan = Bodhan({s: os.environ.get(n, "") for s, n in KEY_ENV.items()}, Cache(HERE / ".bodhan_eval_cache"), Budget(a.max_cost_inr), RunLog())
    rows = [{"id": i, "lang": lang, "source": demo[i],
             "translation": bodhan.translate(demo[i], lang, {"item": f"{i}/{lang}", "check": "build"})["text"]}
            for i in ids for lang in langs]
    for lang in langs:
        sample = sorted((r for r in rows if r["lang"] == lang and plain(r["source"]) and len(r["translation"]) <= 260),
                        key=lambda r: sha(f"audio/{r['id']}/{lang}"))[:a.audio_per_lang]
        for r in sample:
            name = f"{r['id']}.{lang}.wav"
            (out / "audio" / name).write_bytes(speak_long(r["translation"], lang, bodhan, {"item": f"{r['id']}/{lang}", "check": "build"}))
            r["audio"] = f"audio/{name}"
    rows = inject.inject(rows, 0.5, "7", out, out, out / "audio", bodhan)
    with open(out / "unseen.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} rows, {sum(bool(r.get('labels')) for r in rows)} with errors, spent INR {bodhan.budget.spent:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
