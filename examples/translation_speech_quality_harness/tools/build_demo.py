"""Build the demo dataset once. Costs Bodhan credit; every call is cached, so a rerun is free.

python tools/build_demo.py --wmt-dir <folder with en-hi_IN.jsonl, en-ta_IN.jsonl, en-bn_IN.jsonl> --env-file ../../../.env

1. Sources: 24 news sentences from WMT24++ (google/wmt24pp, Apache-2.0; the first 24 by SHA-256 of the English text
   among news segments of 60-300 characters and 10+ words that no language marks as a bad source), plus the 16 items in
   data/own_sources.en.jsonl, written for this recipe.
2. Translations: Indic-Translate, greedy, for every item and language. WMT post-edits become the `reference`.
3. Audio: Indic-Speak for a sample of short, plain items per language (see --audio-per-lang). Saved under data/audio/,
   which git ignores; the README links a public copy.
4. data/demo_clean.jsonl, then the injector writes data/demo.jsonl (seed 7) and data/demo_v2.jsonl (seed 11, for the
   --baseline diff demo).
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))

from bodhan_eval import config, inject  # noqa: E402
from bodhan_eval.__main__ import load_env_file  # noqa: E402
from bodhan_eval.cache import Cache  # noqa: E402
from bodhan_eval.clients import KEY_ENV, Bodhan, Budget, RunLog  # noqa: E402
from bodhan_eval.generate import speak_long  # noqa: E402

LANGS = ["hi", "ta", "bn"]
WMT_NAME = {"hi": "hi_IN", "ta": "ta_IN", "bn": "bn_IN"}


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def wmt_sources(folder: Path, n=24) -> list[dict]:
    files = {lang: [json.loads(x) for x in (folder / f"en-{WMT_NAME[lang]}.jsonl").read_text(encoding="utf-8").splitlines()] for lang in LANGS}
    base = files["hi"]
    assert all(files[lang][i]["source"] == base[i]["source"] for lang in LANGS for i in range(len(base))), "WMT files not aligned"
    ok = [i for i, r in enumerate(base) if r["domain"] == "news" and 60 <= len(r["source"]) <= 300 and r["source"].count(" ") >= 9
          and not any(files[lang][i]["is_bad_source"] for lang in LANGS)]
    ok.sort(key=lambda i: sha(base[i]["source"]))
    return [{"id": f"wmt-{base[i]['segment_id']}", "source": base[i]["source"], "origin": "wmt24pp",
             "references": {lang: files[lang][i]["target"] for lang in LANGS}} for i in ok[:n]]


def plain(text: str) -> bool:
    return not any(mark in text for mark in ("|", "$", "#", "**", "\n"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--wmt-dir", required=True)
    p.add_argument("--env-file")
    p.add_argument("--audio-per-lang", type=int, default=15)
    p.add_argument("--max-cost-inr", type=float, default=6.0)
    p.add_argument("--no-audio", action="store_true", help="translations only; print the speech cost estimate")
    a = p.parse_args()
    if a.env_file:
        load_env_file(a.env_file)
    data = HERE / "data"
    own = [{**json.loads(x), "origin": "own"} for x in (data / "own_sources.en.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    sources = wmt_sources(Path(a.wmt_dir)) + own
    log = RunLog()
    bodhan = Bodhan({s: os.environ.get(n, "") for s, n in KEY_ENV.items()}, Cache(HERE / ".bodhan_eval_cache"), Budget(a.max_cost_inr), log)

    rows = []
    for s in sources:
        for lang in LANGS:
            meta = bodhan.translate(s["source"], lang, {"item": f"{s['id']}/{lang}", "check": "build"})
            row = {"id": s["id"], "lang": lang, "source": s["source"], "translation": meta["text"]}
            if s.get("references"):
                row["reference"] = s["references"][lang]
            rows.append(row)
    print(f"{len(rows)} translations, spent so far INR {bodhan.budget.spent:.3f}")

    sample = []
    for lang in LANGS:
        eligible = [r for r in rows if r["lang"] == lang and plain(r["source"]) and len(r["translation"]) <= 260]
        eligible.sort(key=lambda r: sha(f"audio/{r['id']}/{lang}"))
        sample += eligible[:a.audio_per_lang]
    chars = sum(len(r["translation"]) for r in sample)
    print(f"audio sample: {len(sample)} items, {chars} characters, about INR {chars * config.PRICE_SPEAK_PER_CHAR:.2f}")
    if not a.no_audio:
        (data / "audio").mkdir(exist_ok=True)
        for r in sample:
            wav = speak_long(r["translation"], r["lang"], bodhan, {"item": f"{r['id']}/{r['lang']}", "check": "build"})
            name = f"{r['id']}.{r['lang']}.wav"
            (data / "audio" / name).write_bytes(wav)
            r["audio"] = f"audio/{name}"
        print(f"audio done, spent INR {bodhan.budget.spent:.3f}")

    with open(data / "demo_clean.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for seed, name in (("7", "demo.jsonl"), ("11", "demo_v2.jsonl")):
        out = inject.inject(rows, 0.5, seed, data, data, data / "audio", bodhan)
        with open(data / name, "w", encoding="utf-8", newline="\n") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        counts = {}
        for r in out:
            for lab in r["labels"]:
                counts.setdefault(lab["type"], {}).setdefault(r["lang"], 0)
                counts[lab["type"]][r["lang"]] += 1
        print(name, json.dumps(counts, sort_keys=True))
    print(f"total spent INR {bodhan.budget.spent:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
