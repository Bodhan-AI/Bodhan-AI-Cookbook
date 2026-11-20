"""Compare Indic-Speak voices on the same text: which voice skips, repeats, garbles or rushes more.

python -m bodhan_eval.voices --dataset data/demo.jsonl --langs hi,ta,bn --out report_voices --per-lang 8

Each sampled translation is spoken once per voice (the language's two voices, or --voices A,B). The same speech checks
as the main run then score each recording, and voices.md compares them per language. Only the text is shared, so a
difference between voices is the voice's, not the text's. Every call is cached, so a rerun is free.
"""

import argparse
import hashlib
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import checks_speech as cs
from . import config, dataset
from .__main__ import load_env_file
from .cache import Cache
from .clients import NotRun, RunLog
from .generate import speak_long
from .pipeline import build_clients, parse_langs
from .report import report_cost


def sample(items: list[dict], lang: str, n: int) -> list[dict]:
    """Short plain translations, in a fixed order (hash of the id): speech checks need speakable text."""
    ok = [it for it in items if it["lang"] == lang and it["translation"] and len(it["translation"]) <= 260
          and not any(m in it["translation"] for m in ("|", "$", "#", "**", "\n"))]
    return sorted(ok, key=lambda it: hashlib.sha256(f"voices/{it['id']}".encode()).hexdigest())[:n]


def score(item: dict, voice: str, bodhan) -> dict:
    ctx = {"item": f"{item['id']}/{item['lang']}", "check": f"voice:{voice}"}
    row = {"id": item["id"], "voice": voice}
    try:
        x, sr = cs.read_samples(speak_long(item["translation"], item["lang"], bodhan, ctx, voice=voice))
        st = cs.audio_stats(x, sr)
        problems = [c["category"] for c in cs.stats_candidates(st) + cs.sound_candidates(x, sr, item["translation"])]
        transcript, _ = cs.transcribe_long(x, sr, item["lang"], bodhan, ctx)
        al = cs.align(item["translation"], transcript)
    except NotRun as e:
        return {**row, "not_run": e.reason}
    shape = {"truncation": "speech_omission", "omission": "speech_omission", "repetition": "speech_repetition"}
    problems += [shape[f["shape"]] for f in al["findings"]]
    return {**row, "seconds": st["voiced_s"], "s_per_char": round(st["voiced_s"] / max(1, cs.spoken_chars(item["translation"])), 4),
            "similarity": al["similarity"], "problems": sorted(set(problems))}


def summarize(rows: list[dict]) -> dict:
    done = [r for r in rows if "not_run" not in r]
    med = lambda k: round(statistics.median(r[k] for r in done), 3) if done else None  # noqa: E731
    cats = {}
    for r in done:
        for c in r["problems"]:
            cats[c] = cats.get(c, 0) + 1
    return {"items": len(done), "not_run": len(rows) - len(done), "median_transcript_similarity": med("similarity"),
            "median_seconds_per_char": med("s_per_char"), "items_with_problems": sum(bool(r["problems"]) for r in done),
            "problems": dict(sorted(cats.items()))}


def write(out: Path, per_lang: dict, rows: list[dict], cost: dict):
    out.mkdir(parents=True, exist_ok=True)
    (out / "voices.json").write_text(json.dumps({"per_language": per_lang, "items": rows, "cost": cost}, ensure_ascii=False,
                                                sort_keys=True, indent=1) + "\n", encoding="utf-8")
    lines = ["# Voice comparison", "", "Same text, one recording per voice, scored by the same speech checks. "
             "Transcript similarity is higher-is-better; problems are items with at least one speech finding.", ""]
    lines += ["| Language | Voice | items | items with problems | transcript similarity (median) | s per char (median) | problems |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for lang, by_voice in per_lang.items():
        for voice, s in by_voice.items():
            probs = ", ".join(f"{k} {v}" for k, v in s["problems"].items()) or "none"
            lines.append(f"| {lang} | {voice} | {s['items']} | {s['items_with_problems']} | {s['median_transcript_similarity']} | "
                         f"{s['median_seconds_per_char']} | {probs} |")
    lines += ["", f"Model cost: INR {cost.get('total_inr', 0):.2f}.", ""]
    (out / "voices.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None, bodhan=None) -> int:
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.voices", description="Compare Indic-Speak voices on the same text.")
    p.add_argument("--dataset", required=True)
    p.add_argument("--langs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--voices", help="comma-separated voices to compare (default: the language's two voices)")
    p.add_argument("--per-lang", type=int, default=8, help="items spoken per language and voice")
    p.add_argument("--max-cost-inr", type=float, default=10.0)
    p.add_argument("--cache-dir", default=".bodhan_eval_cache")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--env-file")
    a = p.parse_args(argv)
    if a.env_file:
        load_env_file(a.env_file)
    langs, warnings = parse_langs(a.langs)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    items, _ = dataset.load(a.dataset, langs)
    log = RunLog()
    if bodhan is None:
        bodhan, _ = build_clients(a, log, Cache(a.cache_dir))
    bodhan.log = log
    jobs = [(it, v) for lang in sorted(langs) for it in sample(items, lang, a.per_lang)
            for v in (a.voices.split(",") if a.voices else config.voices(lang))]
    with ThreadPoolExecutor(max_workers=max(1, a.concurrency)) as pool:
        rows = list(pool.map(lambda job: {**score(job[0], job[1], bodhan), "lang": job[0]["lang"]}, jobs))
    per_lang = {}
    for lang in sorted(langs):
        voices = sorted({r["voice"] for r in rows if r["lang"] == lang})
        if voices:
            per_lang[lang] = {v: summarize([r for r in rows if r["lang"] == lang and r["voice"] == v]) for v in voices}
    write(Path(a.out), per_lang, sorted(rows, key=lambda r: (r["lang"], r["id"], r["voice"])), report_cost(log))
    print(f"compared {len(jobs)} recordings; report in {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
