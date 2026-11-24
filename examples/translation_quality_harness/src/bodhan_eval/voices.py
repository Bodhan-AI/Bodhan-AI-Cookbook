"""Compare Indic-Speak voices or generation settings on the same text.

    python -m bodhan_eval.voices --dataset data/demo.jsonl --lang hi \
        --configs Kavya,Suhani,Amit,Kavya:news --sample 8 --out outputs/voices_hi

Each config is ``voice`` or ``voice:style``. The same deterministic sample of clean items is
synthesized with every config, back-transcribed, and run through the speech checks; the report
ranks configs by how faithfully the words come back (comparable-word coverage, CER) and by how
many omission / repetition / garbled candidates they trigger.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from bodhan_eval.cache import Cache
from bodhan_eval.calibrate import DEFAULT_THRESHOLDS
from bodhan_eval.clients import Bodhan, Budget, BudgetExceeded, ModelError
from bodhan_eval.pipeline import hash01, synthesize
from bodhan_eval.speechchecks import analyze_audio, audio_candidates, transcript_candidates
from bodhan_eval.textchecks import check_script


def pick_items(path: Path, lang: str, n: int) -> list[dict]:
    """Items to read aloud, in a fixed hash order: labelled-clean items first (``labels: []``), then
    unlabelled ones that pass the script check. Anything else would test the text, not the voice."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    rows = [r for r in rows if r.get("lang") == lang and r.get("translation") and not r.get("audio")]
    known_clean = [r for r in rows if r.get("labels") == []]
    unlabelled = [r for r in rows if "labels" not in r and not check_script(r["source"], r["translation"], lang)]
    order = sorted(known_clean, key=lambda r: hash01("voices|" + r["id"])) + sorted(unlabelled, key=lambda r: hash01("voices|" + r["id"]))
    return order[:n]


def evaluate_config(bodhan: Bodhan, items: list[dict], lang: str, voice: str, style: str | None) -> dict:
    per_item, cats = [], Counter()
    for it in items:
        wav, _, cost = synthesize(bodhan, it["translation"], lang, voice, style)
        audio = analyze_audio(wav)
        tr = bodhan.transcribe(wav, lang)
        cands, m = transcript_candidates(it["translation"], tr["text"])
        cands += audio_candidates(audio, it["translation"])
        flagged = sorted({c.category for c in cands if c.score >= DEFAULT_THRESHOLDS[c.category]})
        cats.update(flagged)
        chars = len(it["translation"].replace(" ", ""))
        per_item.append({"id": it["id"], "cer": m["cer"], "word_coverage": m.get("word_coverage"), "flags": flagged,
                         "duration_s": audio["duration_s"], "chars_per_s": round(chars / audio["speech_s"], 2) if audio.get("speech_s") else None,
                         "transcript": tr["text"], "tts_cost_inr": round(cost, 4)})

    def mean(key):
        vals = [r[key] for r in per_item if r[key] is not None]
        return round(statistics.mean(vals), 4) if vals else None

    return {"voice": voice, "style": style or "neutral", "items": len(per_item), "mean_word_coverage": mean("word_coverage"),
            "mean_cer": mean("cer"), "mean_chars_per_s": mean("chars_per_s"), "items_flagged": sum(1 for r in per_item if r["flags"]),
            "flags_by_category": dict(sorted(cats.items())), "per_item": per_item}


def write_report(out: Path, lang: str, results: list[dict]) -> None:
    ranked = sorted(results, key=lambda r: (r["items_flagged"], -(r["mean_word_coverage"] or 0), r["mean_cer"] or 1))
    lines = [f"# Indic-Speak config comparison ({lang})", "",
             f"Same {ranked[0]['items'] if ranked else 0} clean items per config, back-transcribed with Indic-Transcribe. "
             "Ranked by items with speech flags, then comparable-word coverage (higher is better), then CER.", "",
             "| rank | voice | style | items flagged | word coverage | CER | chars/s | flags |", "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        flags = ", ".join(f"{c} {n}" for c, n in r["flags_by_category"].items()) or "–"
        lines.append(f"| {i} | {r['voice']} | {r['style']} | {r['items_flagged']} | {r['mean_word_coverage']} | {r['mean_cer']} | "
                     f"{r['mean_chars_per_s']} | {flags} |")
    out.mkdir(parents=True, exist_ok=True)
    (out / "voices.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "voices.json").write_text(json.dumps(ranked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.voices", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True, type=Path)
    p.add_argument("--lang", required=True)
    p.add_argument("--configs", required=True, help="comma-separated voice or voice:style, e.g. Kavya,Amit,Kavya:news")
    p.add_argument("--sample", type=int, default=8)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--max-cost-inr", type=float, default=5.0)
    p.add_argument("--cache-dir", type=Path, default=Path(".cache"))
    args = p.parse_args(argv)
    load_dotenv()

    items = pick_items(args.dataset, args.lang, args.sample)
    if not items:
        print(f"no clean items with a translation for {args.lang}", file=sys.stderr)
        return 2
    budget = Budget(args.max_cost_inr)
    bodhan = Bodhan(Cache(args.cache_dir), budget)
    results = []
    for config in (c.strip() for c in args.configs.split(",") if c.strip()):
        voice, _, style = config.partition(":")
        try:
            results.append(evaluate_config(bodhan, items, args.lang, voice, style or None))
        except (ModelError, BudgetExceeded) as e:
            print(f"{config}: skipped ({e})", file=sys.stderr)
    write_report(args.out, args.lang, results)
    print((args.out / "voices.md").read_text(encoding="utf-8"))
    print(f"spent ₹{budget.spent:.2f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
