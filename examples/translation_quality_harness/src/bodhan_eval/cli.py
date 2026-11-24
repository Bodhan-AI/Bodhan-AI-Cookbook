"""python -m bodhan_eval --dataset data.jsonl --langs hi,ta,bn --out ./report [--generate] [--baseline DIR]
[--calibration labels.jsonl] [--max-cost-inr 50]"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from bodhan_eval import calibrate, pipeline, report
from bodhan_eval.cache import Cache
from bodhan_eval.clients import LLM, Bodhan, Budget, ModelError
from bodhan_eval.dashboard import write_dashboard
from bodhan_eval.langs import SPEECH_PRODUCTION, validate_langs

log = logging.getLogger("bodhan_eval")


class JsonFormatter(logging.Formatter):
    SKIP = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        out = {"t": round(record.created, 3), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        out.update({k: v for k, v in vars(record).items() if k not in self.SKIP})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(out: Path, verbose: bool) -> None:
    root = logging.getLogger("bodhan_eval")
    root.setLevel(logging.INFO)
    root.handlers.clear()
    fh = logging.FileHandler(out / "run.log.jsonl", mode="w", encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root.addHandler(console)
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m bodhan_eval", description="Translation and speech quality harness for Bodhan Indic models.")
    p.add_argument("--dataset", required=True, type=Path, help="JSONL with id, source, lang (+ optional reference, translation, audio, labels)")
    p.add_argument("--langs", required=True, help="comma-separated target codes, e.g. hi,ta,bn")
    p.add_argument("--out", required=True, type=Path, help="report directory")
    p.add_argument("--generate", action="store_true", help="create missing translations (and audio for a sample) with Bodhan models")
    p.add_argument("--baseline", type=Path, help="previous report directory to diff against")
    p.add_argument("--calibration", type=Path, nargs="+", metavar="FILE",
                   help="human label files: JSONL of item labels ({id, lang?, labels}) and/or per-flag verdicts "
                        "({id, lang, category, verdict}), or an answered review_queue.csv")
    p.add_argument("--thresholds", type=Path, help="reuse thresholds from a previous calibration.json instead of tuning on this run's labels")
    p.add_argument("--max-cost-inr", type=float, default=50.0, help="hard cap on Bodhan spend for this run (default 50)")
    p.add_argument("--speech-sample", type=float, default=0.3, help="share of items to synthesize audio for with --generate (default 0.3)")
    p.add_argument("--no-judge", action="store_true", help="skip the LLM judge (deterministic + round-trip checks only)")
    p.add_argument("--judge-repeats", type=int, default=20, help="items re-judged to measure judge consistency (0 to skip)")
    p.add_argument("--cache-dir", type=Path, default=Path(".cache"), help="model output cache (default ./.cache)")
    p.add_argument("--concurrency", type=int, default=4, help="parallel API calls per model (default 4)")
    p.add_argument("--fail-on-regression", type=int, metavar="N", help="CI mode: exit 3 if more than N items regressed vs --baseline")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    load_dotenv()
    started = time.time()
    args.out.mkdir(parents=True, exist_ok=True)
    setup_logging(args.out, args.verbose)

    try:
        langs = validate_langs([x.strip() for x in args.langs.split(",") if x.strip()])
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    preview = [lang for lang in langs if lang.split("-")[0] not in SPEECH_PRODUCTION]
    if preview:
        log.warning("speech for %s is Indic-Speak preview tier; speech checks still run", preview)
    if not args.dataset.exists():
        print(f"error: dataset not found: {args.dataset}", file=sys.stderr)
        return 2
    if args.baseline and not (args.baseline / "items.jsonl").exists():
        print(f"error: baseline has no items.jsonl: {args.baseline}", file=sys.stderr)
        return 2

    items, load_stats = pipeline.load_dataset(args.dataset, langs, args.calibration)
    opts = pipeline.Options(langs=langs, generate=args.generate, judge=not args.no_judge, speech_sample=args.speech_sample,
                            max_cost_inr=args.max_cost_inr, judge_repeats=args.judge_repeats, concurrency=args.concurrency)
    cache = Cache(args.cache_dir)
    budget = Budget(args.max_cost_inr)
    bodhan = Bodhan(cache, budget, concurrency=args.concurrency)
    llm = None
    if opts.judge:
        try:
            llm = LLM(cache, concurrency=args.concurrency)
        except ModelError as e:
            log.warning("LLM judge disabled: %s", e)
    print(f"evaluating {len(items)} items ({len(load_stats['bad'])} bad lines skipped)", file=sys.stderr)

    states = pipeline.run(items, bodhan, llm, opts, args.out / "audio")

    # Calibrate on labelled items, then cut flags with the tuned thresholds.
    pipeline.finalize(states, calibrate.DEFAULT_THRESHOLDS)
    labelled = [st for st in states if st.item.get("labels") is not None and st.status == "ok"]

    def rows(sts, thr):
        return [{"lang": st.item["lang"], "flags": pipeline.cut(st.metrics["clusters"], thr), "labels": st.item["labels"]} for st in sts]

    # Honest numbers: with enough labels, tune on one half and report the targets on the other.
    held_out = None
    fixed = None
    if args.thresholds:
        fixed = {**calibrate.DEFAULT_THRESHOLDS, **json.loads(args.thresholds.read_text(encoding="utf-8-sig"))["thresholds"]}
    elif len(labelled) >= calibrate.MIN_HOLDOUT:
        tune_half = [st for st in labelled if pipeline.hash01("split|" + st.key) < 0.5]
        test_half = [st for st in labelled if st not in tune_half]
        half_thr, _ = calibrate.tune_thresholds([(st.metrics["clusters"], st.item["labels"]) for st in tune_half])
        held_out = {**calibrate.evaluate(rows(test_half, half_thr)), "tuned_on_items": len(tune_half)}

    if fixed:
        thresholds, notes = fixed, {"source": f"--thresholds {args.thresholds.name} (not tuned on this run)"}
    else:
        thresholds, notes = calibrate.tune_thresholds([(st.metrics["clusters"], st.item["labels"]) for st in labelled])
    pipeline.finalize(states, thresholds)
    consistency = pipeline.judge_consistency(states, llm, args.judge_repeats) if llm and args.judge_repeats else {"items": 0}
    metrics = calibrate.evaluate(rows(labelled, thresholds))
    per_check = calibrate.evaluate_checks([{"labels": st.item["labels"], "candidates": [{"check": c.check, "category": c.category} for c in st.candidates]}
                                           for st in labelled])
    basis = held_out or metrics
    human = calibrate.human_review(load_stats["judgments"], {st.key_tuple: st.metrics["clusters"] for st in states}, thresholds) \
        if load_stats["judgments"] else None
    calibration = {**metrics, "human_review": human, "per_check": per_check, "thresholds": thresholds, "threshold_tuning": notes,
                   "judge_consistency": consistency,
                   "held_out": held_out, "targets": calibrate.target_status(basis, consistency.get("same_verdict_rate")),
                   "target_notes": calibrate.explain_misses(basis, consistency),
                   "targets_measured_on": "all labelled items, with thresholds from --thresholds" if fixed else
                   "held-out half (thresholds tuned on the other half)" if held_out else "all labelled items (too few to split)"}

    records = [report.item_record(st) for st in states]
    sources = {(st.item["id"], st.item["lang"]): st.item["source"] for st in states}
    report.write_items(args.out / "items.jsonl", records)
    summary = report.build_summary(records, sources, load_stats, calibration)
    diff = report.compare(records, args.baseline) if args.baseline else None
    if diff:
        base_summary = args.baseline / "summary.json"
        if base_summary.exists():  # quality-score change per language
            base_langs = json.loads(base_summary.read_text(encoding="utf-8")).get("per_language", {})
            for lang, m in diff["per_language"].items():
                m["baseline_mqm"] = base_langs.get(lang, {}).get("mqm_score")
                m["current_mqm"] = summary["per_language"].get(lang, {}).get("mqm_score")
        report.write_diff_md(args.out / "diff.md", diff)
        summary["baseline"] = {k: diff[k] for k in ("common_items", "regressed_items", "improved_items", "category_delta")}
    report.dump_json(args.out / "summary.json", summary)
    report.dump_json(args.out / "calibration.json", calibration)
    report.write_summary_md(args.out / "summary.md", summary, records, calibration, diff)
    write_dashboard(args.out / "dashboard.html", records, summary, calibration)
    queued = report.write_review_queue(args.out / "review_queue.csv", states, thresholds)
    report.dump_json(args.out / "run.json", {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "wall_time_s": round(time.time() - started, 1),
        "spent_inr_this_run": round(budget.spent, 4),
        "spent_by_model": {k: round(v, 4) for k, v in budget.by_model.items()},
        "network_calls": budget.calls,
        "max_cost_inr": args.max_cost_inr,
        "llm_model": llm.model if llm else None,
        "review_queue_items": queued,
    })

    print(f"done: {summary['flagged_items']}/{len(records)} items flagged, spent ₹{budget.spent:.2f} this run -> {args.out}", file=sys.stderr)
    if args.fail_on_regression is not None and diff and diff["regressed_items"] > args.fail_on_regression:
        print(f"regression gate: {diff['regressed_items']} items regressed (> {args.fail_on_regression})", file=sys.stderr)
        return 3
    return 0
