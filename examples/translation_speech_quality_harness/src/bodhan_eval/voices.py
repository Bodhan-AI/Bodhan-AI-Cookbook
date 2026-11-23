"""Compare Indic-Speak voices on the same texts: which voice drops, repeats or garbles speech more often.

    python -m bodhan_eval.voices --dataset data/demo.jsonl --voices "hi=Kavya,Amit;ta=Anitha,Arun;bn=Ishita,Sourav" \\
        --sample 15 --out outputs/voices --max-cost-inr 18

For a seeded sample of items per language, every listed voice speaks the same translation (Indic-Speak, the
same chunking as ``--generate``; a voice already synthesised for the text comes from the cache). The speech
checks then run on each clip exactly as in a normal run, with default thresholds. ``voices.md`` and
``voices.json`` give per voice: items with a speech flag, flags per category, the mean transcript character
error rate, and the median speaking rate; plus a paired count (items only one voice of the pair got flagged
on), because the texts are identical and only the voice differs. Audio goes to ``<out>/audio/<voice>/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .checks import SPEECH_CHECKS
from .cli import setup_logging
from .config import defaults, lang_info
from .dataset import load_dataset
from .generate import synthesize
from .models import Item, ItemResult
from .pipeline import RunOptions, _context, _fuse_all, _run_items, _speaking_rate, prepass_stats

log = logging.getLogger("bodhan_eval.voices")
SPEECH = ("speech_omission", "speech_repetition", "speech_garbled")


def parse_voices(spec: str) -> dict[str, list[str]]:
    """'hi=Kavya,Amit;ta=Anitha,Arun' -> {'hi': ['Kavya', 'Amit'], 'ta': ['Anitha', 'Arun']}"""
    out: dict[str, list[str]] = {}
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        lang, _, names = part.partition("=")
        voices = [v.strip() for v in names.split(",") if v.strip()]
        if not lang.strip() or len(voices) < 2:
            raise ValueError(f"expected lang=VoiceA,VoiceB in {part!r}")
        out[lang.strip()] = voices
    return out


def sample(items: list[Item], lang: str, n: int, seed: int) -> list[Item]:
    pool = [it for it in items if it.lang == lang and it.translation]
    pool.sort(key=lambda it: hashlib.sha256(f"{seed}|{it.id}".encode()).hexdigest())
    return pool[:n]


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: one per (lang, voice, item) with 'flags' (speech categories), 'cer', 'rate'."""
    out: dict[str, Any] = {}
    for lang in sorted({r["lang"] for r in rows}):
        voices: dict[str, Any] = {}
        for voice in dict.fromkeys(r["voice"] for r in rows if r["lang"] == lang):
            rs = [r for r in rows if r["lang"] == lang and r["voice"] == voice]
            cers = [r["cer"] for r in rs if r["cer"] is not None]
            rates = [r["rate"] for r in rs if r["rate"] is not None]
            voices[voice] = {
                "items": len(rs),
                "flagged_items": sum(bool(r["flags"]) for r in rs),
                "flags": dict(sorted(Counter(c for r in rs for c in r["flags"]).items())),
                "mean_cer": round(statistics.mean(cers), 4) if cers else None,
                "median_chars_per_second": round(statistics.median(rates), 2) if rates else None,
            }
        names = list(voices)
        flagged = {v: {r["id"] for r in rows if r["lang"] == lang and r["voice"] == v and r["flags"]} for v in names}
        pairs = {
            f"{a} vs {b}": {"only_" + a: len(flagged[a] - flagged[b]), "only_" + b: len(flagged[b] - flagged[a])}
            for i, a in enumerate(names)
            for b in names[i + 1 :]
        }
        out[lang] = {"voices": voices, "paired": pairs}
    return out


def render(summary: dict[str, Any]) -> str:
    lines = ["# Voice comparison", "", "Same texts, every voice; speech checks with default thresholds.", ""]
    for lang, s in summary.items():
        lines += [f"## {lang}", "", "| Voice | Items | Items with a speech flag | Flags | Mean transcript CER | Median chars/s |",
                  "|---|---:|---:|---|---:|---:|"]  # fmt: skip
        for voice, v in s["voices"].items():
            flags = ", ".join(f"{k} {n}" for k, n in v["flags"].items()) or "none"
            cer = "–" if v["mean_cer"] is None else f"{100 * v['mean_cer']:.1f}%"
            rate = "–" if v["median_chars_per_second"] is None else f"{v['median_chars_per_second']:.1f}"
            lines.append(f"| {voice} | {v['items']} | {v['flagged_items']} | {flags} | {cer} | {rate} |")
        for pair, counts in s["paired"].items():
            lines.append(f"\nPaired ({pair}): " + ", ".join(f"{k.replace('only_', 'flagged only with ')} {n}" for k, n in counts.items()))
        lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    spec = parse_voices(args.voices)
    items, _ = load_dataset(args.dataset, set(spec))
    opts = RunOptions(dataset=args.dataset, out=args.out, max_cost_inr=args.max_cost_inr, judge=False)
    args.out.mkdir(parents=True, exist_ok=True)
    ctx = _context(opts)
    rows: list[dict[str, Any]] = []
    try:
        thresholds = dict(defaults()["flag_threshold"])
        for lang, voices in spec.items():
            chosen = sample(items, lang, args.sample, args.seed)
            for voice in voices:
                if voice not in lang_info(lang).voices:
                    log.warning("%s is not a listed %s voice; trying it anyway", voice, lang)
                ctx.options["voices"] = {lang: voice}
                audio_dir = args.out / "audio" / voice
                audio_dir.mkdir(parents=True, exist_ok=True)
                clips = [Item(id=it.id, lang=lang, source=it.source, translation=it.translation) for it in chosen]
                with ThreadPoolExecutor(max_workers=opts.concurrency) as pool:  # the client's limiter still applies
                    for it, path in zip(clips, pool.map(synthesize, clips, [ctx] * len(clips), [audio_dir] * len(clips))):
                        it.audio = path
                ctx.stats = prepass_stats(clips, ctx)
                results = [ItemResult(item=it) for it in clips]
                _run_items(results, [cls() for cls in SPEECH_CHECKS], ctx, opts.concurrency)
                _fuse_all(results, thresholds, ctx)
                for r in results:
                    rows.append({
                        "lang": lang, "voice": voice, "id": r.item.id,
                        "flags": [f.category for f in r.flags if f.category in SPEECH],
                        "cer": r.metrics.get("asr_cer"), "rate": _speaking_rate(r.item, ctx),
                    })  # fmt: skip
                log.info("%s %s: %d clips checked (spent ₹%.2f so far)", lang, voice, len(results), ctx.budget.total_spent)
    finally:
        ctx.events.close()
        ctx.cache.close()
    summary = summarise(rows)
    (args.out / "voices.json").write_text(json.dumps({"summary": summary, "items": rows}, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (args.out / "voices.md").write_text(render(summary), encoding="utf-8", newline="\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bodhan_eval.voices", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--voices", required=True, help='e.g. "hi=Kavya,Amit;ta=Anitha,Arun"')
    ap.add_argument("--sample", type=int, default=15, help="items per language")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-cost-inr", type=float, required=True)
    ap.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    args = ap.parse_args(argv)
    setup_logging(args.log_level)
    try:
        run(args)
    except (OSError, ValueError) as e:
        log.error("voice comparison failed: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
