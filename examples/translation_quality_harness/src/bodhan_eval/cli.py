"""Command line entry point: python -m bodhan_eval --dataset ... --langs ... --out ..."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import httpx

from . import __version__, log
from .budget import CostTracker
from .cache import Cache, atomic_write_text
from .calibration import evaluate, rescore, tune
from .clients.bodhan import SPEAK, TRANSCRIBE, TRANSLATE, BodhanClient
from .clients.llm import LLMClient
from .config import Settings, Thresholds, load_dotenv_if_present
from .dataset import load
from .judge.judge import Judge
from .judge.rubric import RUBRIC_VERSION
from .languages import PRODUCTION_TIER, get_language
from .models import ItemResult
from .pipeline import Pipeline, RunOptions
from .reporting import dashboard, diff, review, summary

EXIT_OK, EXIT_INPUT, EXIT_REGRESSION = 0, 2, 3


def _cost_cap(value: str) -> float:
    cap = float(value)
    if not math.isfinite(cap) or cap < 0:
        raise argparse.ArgumentTypeError("cost cap must be finite and nonnegative")
    return cap


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bodhan_eval", description="Translation and speech quality harness for Bodhan Indic-Translate and Indic-Speak.")
    p.add_argument("--dataset", required=True, help="JSONL with id, source, lang and optional reference, translation, audio, labels")
    p.add_argument("--langs", default=None, help="comma-separated language codes to evaluate (default: every language in the dataset)")
    p.add_argument("--out", required=True, help="report directory")
    p.add_argument("--generate", action="store_true", help="create missing translations and audio with indic-translate and indic-speak")
    p.add_argument("--baseline", default=None, help="previous report directory to diff against")
    p.add_argument("--calibration", default=None, help="labelled JSONL used to measure the harness and tune thresholds")
    p.add_argument("--max-cost-inr", type=_cost_cap, default=50.0, help="hard cap on Bodhan spend for this run (default 50)")
    p.add_argument("--judge", choices=["all", "suspicious", "off"], default="all", help="which items the LLM judge reads (default all)")
    p.add_argument("--judge-votes", type=int, default=1, help="judge samples per item, majority-voted (default 1)")
    p.add_argument("--judge-repeats", type=int, default=1, help="re-judge a sample this many times to measure consistency (default 1)")
    p.add_argument("--judge-repeat-sample", type=int, default=30, help="items in the consistency sample (default 30)")
    p.add_argument("--speech", choices=["auto", "off"], default="auto", help="speech checks: auto runs them when audio exists or --generate is set")
    p.add_argument("--speech-sample", type=float, default=1.0, help="fraction of items to synthesise with --generate (deterministic by id)")
    p.add_argument("--voice", default=None, help="indic-speak voice for generation (default: first voice listed for the language)")
    p.add_argument("--thresholds", default=None, help="thresholds JSON (for example a previous report's thresholds.json)")
    p.add_argument("--cache-dir", default=os.environ.get("BODHAN_EVAL_CACHE", ".bodhan_cache"), help="cache directory (default .bodhan_cache)")
    p.add_argument("--no-cache", action="store_true", help="ignore cached model outputs (they are still written)")
    p.add_argument("--offline", action="store_true", help="use cached model outputs only; never make model API requests")
    p.add_argument("--fail-on-regression", type=int, default=None, metavar="N", help="with --baseline, exit 3 when more than N items regress")
    p.add_argument("--max-items", type=int, default=None, help="evaluate only the first N items (smoke runs)")
    p.add_argument("--concurrency", type=int, default=None, help="override every per-model concurrency cap")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"bodhan_eval {__version__}")
    return p


def _run_id(dataset: Path, args: argparse.Namespace) -> str:
    h = hashlib.sha256(dataset.read_bytes())
    h.update(json.dumps({k: v for k, v in sorted(vars(args).items()) if k not in {"verbose"}}, default=str).encode())
    return h.hexdigest()[:12]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv_if_present()
    dataset_path = Path(args.dataset)
    if not dataset_path.is_file():
        print(f"error: dataset not found: {dataset_path}", file=sys.stderr)
        return EXIT_INPUT
    if args.calibration and not Path(args.calibration).is_file():
        print(f"error: calibration file not found: {args.calibration}", file=sys.stderr)
        return EXIT_INPUT
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    run_id = _run_id(dataset_path, args)
    log.setup(out / "run.log.jsonl", run_id=run_id, verbose=args.verbose)
    try:
        return asyncio.run(_run(args, dataset_path, out, run_id))
    except KeyboardInterrupt:
        log.warn("interrupted; completed model calls are cached, rerun to resume")
        return 130
    finally:
        log.close()


async def _run(args: argparse.Namespace, dataset_path: Path, out: Path, run_id: str) -> int:
    started = time.perf_counter()
    settings = Settings.from_env()
    settings.offline = args.offline
    if args.concurrency:
        settings.concurrency = {k: args.concurrency for k in settings.concurrency}
    thresholds = Thresholds.load(args.thresholds)
    cache = Cache(args.cache_dir, enabled=not args.no_cache)
    budget = CostTracker(max_inr=args.max_cost_inr)

    langs = [x.strip() for x in args.langs.split(",") if x.strip()] if args.langs else None
    for code in langs or []:
        lang = get_language(code)
        if not lang.known:
            log.warn("unknown language code; generic checks only", lang=code)
        elif code not in PRODUCTION_TIER:
            log.info("language outside the indic-speak production tier", lang=code, tier=lang.tier)

    loaded, bad = load(dataset_path)
    filtered = 0
    if langs:
        before = len(loaded)
        loaded = [li for li in loaded if li.item.lang in langs]
        filtered = before - len(loaded)
    if args.max_items:
        loaded = loaded[: args.max_items]
    cal_loaded, cal_bad = load(args.calibration, origin="calibration") if args.calibration else ([], [])
    if langs and cal_loaded:
        log.info("calibration items are used for every language they cover", count=len(cal_loaded))
    labeled_ids = {li.uid for li in loaded if li.item.has_labels_field} | {li.uid for li in cal_loaded}
    sources = {li.uid: li.item.source for li in loaded + cal_loaded}
    log.info("loaded", count=len(loaded), path=str(dataset_path), rejected=len(bad), calibration=len(cal_loaded))

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=64, max_keepalive_connections=32)) as http:
        bodhan = BodhanClient(settings, cache, budget, http)
        llm = LLMClient(settings, cache, http)
        judge = Judge(llm, votes=args.judge_votes) if settings.llm_configured and args.judge != "off" else None
        for model, reason in sorted(bodhan.disabled.items()):
            log.warn("bodhan model unavailable; dependent checks are skipped", model=model, error=reason)
        if judge is None:
            log.info("LLM judge disabled; running without it" if args.judge == "off" else "LLM judge not configured; running without it")
        opts = RunOptions(
            out_dir=out,
            generate=args.generate,
            speech=args.speech,
            speech_sample=args.speech_sample,
            judge_policy=args.judge,
            judge_votes=args.judge_votes,
            judge_repeats=args.judge_repeats,
            judge_repeat_sample=args.judge_repeat_sample,
            voice=args.voice,
        )
        state = await Pipeline(thresholds, bodhan, judge, opts).run(loaded + cal_loaded)

    cal_ids = {li.uid for li in cal_loaded}
    ds_results = [r for r in state.results if r.id not in cal_ids]
    cal_results = [r for r in state.results if r.id in cal_ids]

    calibration_report: dict = {"thresholds_before": thresholds.flag}
    final_th = thresholds
    if cal_results:
        calibration_report["calibration_set_before_tuning"] = evaluate(cal_results, thresholds, cal_ids)
        final_th, tuning_log = tune(cal_results, thresholds, cal_ids)
        calibration_report["tuning"] = tuning_log
        calibration_report["calibration_set"] = evaluate(cal_results, final_th, cal_ids)
    ds_results = [rescore(r, final_th, sources.get(r.id, "")) if r.status == "scored" else r for r in ds_results]
    cal_results = [rescore(r, final_th, sources.get(r.id, "")) if r.status == "scored" else r for r in cal_results]
    ds_labeled = labeled_ids - cal_ids
    if ds_labeled:
        calibration_report["dataset"] = evaluate(ds_results, final_th, ds_labeled)
    calibration_report["thresholds_after"] = final_th.flag
    calibration_report["judge_consistency"] = state.consistency
    calibration_report["notes"] = (
        "Metrics are computed from labels; 'dataset' uses labels inside --dataset and 'calibration_set' uses --calibration. "
        "Thresholds are tuned only on the calibration set."
    )

    diff_data = None
    if args.baseline:
        try:
            diff_data = diff.compare(ds_results, diff.load_items(args.baseline))
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
            log.warn("baseline unreadable; diff skipped", error=str(exc))

    cost = _cost_section(budget, ds_results + cal_results)
    config = {
        "dataset": args.dataset,
        "langs": langs,
        "generate": args.generate,
        "judge_policy": args.judge,
        "judge_votes": args.judge_votes,
        "llm_model": settings.llm_model if judge else None,
        "rubric_version": RUBRIC_VERSION,
        "harness_version": __version__,
        "calibration": args.calibration,
        "baseline": args.baseline,
        "max_cost_inr": args.max_cost_inr,
        "offline": args.offline,
        "bodhan_models_available": {m: m not in bodhan.disabled for m in (TRANSLATE, SPEAK, TRANSCRIBE)},
    }
    bad_rows = [{"line": b.line, "id": b.id, "error": b.error} for b in bad] + [
        {"line": b.line, "id": b.id, "error": f"calibration: {b.error}"} for b in cal_bad
    ]
    summ = summary.build_summary(
        ds_results,
        bad_rows=bad_rows,
        filtered=filtered,
        cost=cost,
        calibration=calibration_report,
        consistency=state.consistency,
        config=config,
        thresholds=final_th.flag,
        diff=diff_data,
    )

    _write_jsonl(out / "items.jsonl", ds_results)
    if cal_results:
        _write_jsonl(out / "calibration_items.jsonl", cal_results)
    atomic_write_text(out / "summary.json", _dump(summ))
    atomic_write_text(out / "summary.md", summary.render_markdown(summ, ds_results, calibration_report, diff_data))
    atomic_write_text(out / "calibration.json", _dump(calibration_report))
    atomic_write_text(out / "thresholds.json", _dump(final_th.model_dump()))
    atomic_write_text(out / "dashboard.html", dashboard.render(ds_results, sources))
    atomic_write_text(out / "review_queue.csv", review.export(ds_results + cal_results, final_th))
    if diff_data is not None:
        atomic_write_text(out / "diff.md", diff.render(diff_data, args.baseline))
        atomic_write_text(out / "diff.json", _dump(diff_data))

    elapsed = time.perf_counter() - started
    run_meta = {
        "run_id": run_id,
        "elapsed_s": round(elapsed, 2),
        "items": len(ds_results),
        "calibration_items": len(cal_results),
        "seconds_per_item": round(elapsed / max(len(ds_results) + len(cal_results), 1), 3),
        "projected_minutes_for_200_items": round(elapsed / max(len(ds_results) + len(cal_results), 1) * 200 / 60, 2),
        "cache": {"hits": cache.hits, "misses": cache.misses},
        "budget": budget.snapshot(),
        "llm_usage": llm.usage,
        "bodhan_disabled": bodhan.disabled,
        "llm_disabled": llm.disabled,
        "warnings": state.warnings,
    }
    atomic_write_text(out / "run.json", _dump(run_meta))
    cache.close()

    flagged = summ["items"]["flagged"]
    log.info("done", count=len(ds_results), path=str(out), stage=f"flagged={flagged} spent_inr={budget.spent:.4f} elapsed_s={elapsed:.1f}")
    if budget.exhausted:
        log.warn("--max-cost-inr reached: some paid checks were skipped; see summary.md skip reasons")
    if args.fail_on_regression is not None and diff_data and diff_data["headline"]["regressed"] > args.fail_on_regression:
        log.warn("regression gate failed", count=diff_data["headline"]["regressed"])
        return EXIT_REGRESSION
    return EXIT_OK


def _cost_section(budget: CostTracker, results: list[ItemResult]) -> dict:
    attributed = {k: round(v, 4) for k, v in sorted(budget.attributed.items())}
    n = max(sum(1 for r in results if r.status == "scored"), 1)
    speech_items = max(sum(1 for r in results if r.audio), 1)
    text_cost = budget.attributed.get(TRANSLATE, 0.0)
    speech_cost = budget.attributed.get(SPEAK, 0.0) + budget.attributed.get(TRANSCRIBE, 0.0)
    return {
        "attributed_inr": attributed,
        "total_inr": round(sum(budget.attributed.values()), 4),
        "per_100_items_inr": {"text_checks": round(text_cost / n * 100, 4), "speech": round(speech_cost / speech_items * 100, 4)},
        "note": "attributed = cost of every Bodhan output this report used, whether paid now or read from cache",
    }


def _dump(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"


def _write_jsonl(path: Path, results: list[ItemResult]) -> None:
    lines = [json.dumps(r.model_dump(mode="json", exclude={"checks": {"__all__": {"cache"}}}), ensure_ascii=False, sort_keys=True) for r in results]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
