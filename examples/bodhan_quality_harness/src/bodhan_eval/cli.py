"""Command-line runner.

Order of operations is the design: free checks first, then the judge only where
it earns its cost, then the paid audio round-trip only on a deliberate sample.
Every stage degrades gracefully — a missing key disables that stage with a
logged reason instead of failing the run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
import time
from pathlib import Path

from . import calibrate, config, inject, report
from .bodhan import BodhanClient, BudgetExceeded, CostTracker
from .cache import Cache
from .checks import Flag, ItemContext, build_registry
from .checks.base import dedupe_flags
from .checks import speech as speech_checks
from .dataset import load_dataset
from .judge import Judge

log = logging.getLogger("bodhan_eval")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bodhan_eval",
        description="Translation and speech quality harness for Bodhan Indic models.",
    )
    p.add_argument("--dataset", required=False, type=Path,
                   help="JSONL dataset: id, source, lang (+ optional translation, audio, reference, labels)")
    p.add_argument("--langs", required=False,
                   help="comma-separated target languages, e.g. hi,ta,bn")
    p.add_argument("--out", required=False, type=Path, default=Path("report"), help="output directory")
    p.add_argument("--serve", action="store_true",
                   help="launch the interactive web dashboard & live API server")
    p.add_argument("--port", type=int, default=8080,
                   help="port for the interactive web server (default 8080)")
    p.add_argument("--generate", action="store_true",
                   help="generate missing translations and audio with Bodhan models")
    p.add_argument("--baseline", type=Path, default=None,
                   help="previous report directory to diff against")
    p.add_argument("--calibration", type=Path, default=None,
                   help="labelled JSONL used to calibrate thresholds")
    p.add_argument("--max-cost-inr", type=float, default=None,
                   help="hard budget ceiling for model calls")
    p.add_argument("--speech-sample", type=int, default=25,
                   help="max items to run the paid back-transcription on (default 25)")
    p.add_argument("--judge-sample", type=int, default=0,
                   help="also judge this many clean items (0 = only suspicious ones)")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--offline", action="store_true",
                   help="never call a model; run deterministic checks only")
    p.add_argument("--inject", action="store_true",
                   help="self-measure: inject labelled errors into clean items and score against them")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config.load_env()

    if args.serve:
        from .server import run_server
        out_dir = args.out or Path("report")
        return run_server(port=args.port, report_dir=out_dir)

    if not args.dataset or not args.langs:
        parser.error("the following arguments are required: --dataset, --langs")

    out_dir = args.out or Path("report")
    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    cfg = config.RunConfig(
        dataset=args.dataset, langs=langs, out=out_dir,
        generate=args.generate, baseline=args.baseline,
        calibration=args.calibration, max_cost_inr=args.max_cost_inr,
        concurrency=args.concurrency, seed=args.seed, offline=args.offline,
    )

    if not cfg.dataset.exists():
        log.error("dataset not found: %s", cfg.dataset)
        return 2

    return asyncio.run(_run(cfg, args))


async def _run(cfg: config.RunConfig, args) -> int:
    started = time.time()
    loaded = load_dataset(cfg.dataset)
    items = loaded.items
    log.info("loaded %d items (%d skipped) from %s",
             len(items), len(loaded.skipped), cfg.dataset)

    if not items:
        log.error("no usable items — nothing to score")
        return 2

    # --langs is authoritative: score only the requested languages, and say so.
    wanted = set(cfg.langs)
    if wanted:
        kept = [it for it in items if it.lang in wanted]
        dropped = len(items) - len(kept)
        if dropped:
            log.info("filtered %d item(s) outside --langs %s", dropped, sorted(wanted))
        if not kept:
            log.error("no items match --langs %s (dataset has %s)",
                      sorted(wanted), sorted({it.lang for it in items}))
            return 2
        items = kept

    # Optional self-measurement: expand clean items into labelled variants.
    if args.inject:
        expanded: list[ItemContext] = []
        for it in items:
            if it.labels or not it.translation:
                expanded.append(it)
                continue
            for variant in inject.inject_item(it.id, it.source, it.translation, it.lang,
                                              seed=cfg.seed):
                expanded.append(ItemContext(
                    id=variant["id"], source=variant["source"], lang=variant["lang"],
                    translation=variant["translation"], labels=variant["labels"],
                ))
        log.info("injected: %d -> %d items", len(items), len(expanded))
        items = expanded

    thresholds = cfg.thresholds

    # Calibrate thresholds from labels before scoring. Labels come from the
    # scored items and, when --calibration is given, from that file too — the
    # sweep uses the pooled labels but only the dataset items are scored.
    calibration_out = None
    labelled = [it for it in items if it.labels]
    if cfg.calibration:
        if not cfg.calibration.exists():
            log.warning("--calibration file not found: %s", cfg.calibration)
        else:
            extra = load_dataset(cfg.calibration)
            extra_labelled = [it for it in extra.items if it.labels]
            log.info("calibration file contributed %d labelled item(s) (%d skipped)",
                     len(extra_labelled), len(extra.skipped))
            labelled = labelled + extra_labelled
    if labelled:
        records = []
        for it in labelled:
            if not it.translation:
                continue
            sw = max(1, len(it.source.split()))
            tw = len(it.translation.split())
            kinds = {str(l.get("type", "")).lower() for l in it.labels}
            records.append({
                "ratio": tw / sw,
                "label": "omission" if "omission" in kinds else
                         "addition" if "addition" in kinds else None,
            })
        if records:
            sweep = calibrate.sweep_length(records)
            thresholds.length_lo = sweep["chosen"]["lo"]
            thresholds.length_hi = sweep["chosen"]["hi"]
            log.info("calibrated length thresholds: lo=%.2f hi=%.2f (from %d labelled items)",
                     thresholds.length_lo, thresholds.length_hi, len(records))
            calibration_out = {"length_sweep": sweep}

    # ---- tier 0-1: free deterministic checks ----
    registry = build_registry(thresholds)
    predictions: dict[str, list[Flag]] = {}
    checks_run: dict[str, list[str]] = {}
    for it in items:
        flags: list[Flag] = []
        ran: list[str] = []
        for name, _tier, fn in registry:
            try:
                flags.extend(fn(it))
                ran.append(name)
            except Exception as exc:  # noqa: BLE001 - one bad item must not stop the run
                log.warning("check %s failed on %s: %s", name, it.id, exc)
        predictions[it.id] = flags
        checks_run[it.id] = ran

    # ---- model stages ----
    cache = Cache(cfg.cache_dir, enabled=True)
    costs = CostTracker(max_inr=cfg.max_cost_inr)
    judge_stats = None

    async with BodhanClient(cache, costs, offline=cfg.offline,
                            concurrency=cfg.concurrency) as client:
        # --generate: fill in what is missing
        if cfg.generate and not cfg.offline:
            for it in items:
                if not it.translation and config.have_translate():
                    try:
                        it.translation = await client.translate(it.source, it.lang)
                        checks_run[it.id].append("generate.translate")
                    except (BudgetExceeded, RuntimeError) as exc:
                        log.warning("translate skipped for %s: %s", it.id, exc)
                        break
            for it in items:
                if not it.audio and it.translation and config.have_speak():
                    try:
                        audio = await client.speak(it.translation, it.lang)
                        p = cfg.out / "audio"
                        p.mkdir(parents=True, exist_ok=True)
                        f = p / f"{it.id}.{it.lang}.wav"
                        f.write_bytes(audio)
                        it.audio = str(f)
                        checks_run[it.id].append("generate.speak")
                    except (BudgetExceeded, RuntimeError) as exc:
                        log.warning("speak skipped for %s: %s", it.id, exc)
                        break

        # ---- tier 2: judge, only where it pays ----
        judge = Judge(cache, offline=cfg.offline)
        if config.have_judge() and not cfg.offline:
            rng = random.Random(cfg.seed)
            clean_pool = [it for it in items if not it.labels and it.translation]
            sample = set(it.id for it in rng.sample(
                clean_pool, min(args.judge_sample, len(clean_pool)))) if args.judge_sample else set()

            targets = [it for it in items
                       if it.translation and (predictions[it.id] or it.id in sample)]

            async def judge_one(it):
                first = await judge.review(it, predictions[it.id])
                # Re-ask with the cache bypassed. Asking through the cache would
                # return the first answer verbatim and report a meaningless 100%.
                second = await judge.review(it, predictions[it.id], use_cache=False)
                return it, first, second

            judged = agreed = 0
            if targets:
                results = await asyncio.gather(
                    *[judge_one(it) for it in targets], return_exceptions=True)
                for outcome in results:
                    if isinstance(outcome, Exception):
                        log.warning("judge failed on an item: %s", outcome)
                        continue
                    it, first, second = outcome
                    checks_run[it.id].append("judge.llm")
                    judged += 1
                    if first.ok:
                        predictions[it.id].extend(first.flags)
                    if first.ok and second.ok:
                        a = {(f.category, f.span) for f in first.flags}
                        b = {(f.category, f.span) for f in second.flags}
                        agreed += int(a == b)

            judge_stats = {
                "items_judged": judged,
                "consistency_samples": agreed,
                "consistency": round(agreed / judged, 3) if judged else None,
                "note": "consistency is measured by re-asking with the cache bypassed, "
                        "so it reflects the model rather than the cache",
            }
        else:
            log.info("judge disabled (no LLM_BASE_URL/LLM_MODEL/LLM_API_KEY or --offline)")

    # ---- tier 3: speech ----
        audio_items = [it for it in items if it.audio]
        for it in audio_items:
            predictions[it.id].extend(speech_checks.check_audio_signal(it, thresholds))
            checks_run[it.id].append("speech.signal")

        if config.have_transcribe() and not cfg.offline:
            rng = random.Random(cfg.seed)
            sample = audio_items if len(audio_items) <= args.speech_sample else rng.sample(
                audio_items, args.speech_sample)
            for it in sample:
                try:
                    data = Path(it.audio).read_bytes()
                    transcript = await client.transcribe(data, it.lang)
                    expected = it.translation or ""
                    predictions[it.id].extend(
                        speech_checks.check_transcript(it, transcript, expected, thresholds))
                    checks_run[it.id].append("speech.roundtrip")
                except (BudgetExceeded, RuntimeError, OSError) as exc:
                    log.warning("round-trip skipped for %s: %s", it.id, exc)
                    break
        elif audio_items:
            log.info("back-transcription disabled (no BODHAN_TRANSCRIBE_KEY or --offline); "
                     "signal-level speech checks still ran on %d items", len(audio_items))

    # ---- merge duplicate findings before scoring ----
    for iid in predictions:
        predictions[iid] = dedupe_flags(predictions[iid])

    # ---- score and write ----
    metrics = calibrate.score(predictions, items)
    if calibration_out is not None:
        calibration_out["scoring"] = metrics
        calibration_out["judge"] = judge_stats

    summary = report.write_reports(
        cfg.out, items=items, predictions=predictions, checks_run=checks_run,
        metrics=metrics, calibration=calibration_out, cost=costs.summary(),
        runtime_s=time.time() - started, cache_stats=cache.stats(),
        skipped=loaded.skipped, judge_stats=judge_stats,
    )

    if cfg.baseline:
        diff = report.write_diff(cfg.out, summary, cfg.baseline)
        if diff:
            (cfg.out / "diff.md").write_text(diff, encoding="utf-8")
            log.info("wrote diff.md against %s", cfg.baseline)
        else:
            log.warning("baseline %s has no summary.json — skipped diff", cfg.baseline)

    overall = metrics["overall"]
    log.info("done: %d items, %d flags, recall %.1f%%, false alarms %.1f%%, cost Rs %.2f",
             len(items), summary["totals"]["flags"],
             overall["recall"] * 100, overall["false_alarm_rate"] * 100,
             costs.spent)
    log.info("report written to %s (digest %s)", cfg.out, summary["digest"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
