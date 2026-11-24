"""Command line: python -m bodhan_eval --dataset data.jsonl --langs hi,ta,bn --out ./report [...]"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from .config import Settings
from .dataset import load_dataset
from .langs import parse_langs
from .pipeline import Options, Run
from .redact import redact
from .report import write_report

_STD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}


class JsonLines(logging.Handler):
    def __init__(self, path: Path):
        super().__init__()
        self.fh = open(path, "w", encoding="utf-8", newline="\n")  # noqa: SIM115

    def emit(self, record):
        extra = {k: v for k, v in record.__dict__.items() if k not in _STD}
        row = {"level": record.levelname, "logger": record.name, "msg": redact(record.getMessage()), **{k: redact(v) for k, v in extra.items()}}
        if record.exc_info:
            row["exc"] = self.format(record).splitlines()[-1]
        self.fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self.fh.flush()

    def close(self):
        self.fh.close()
        super().close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bodhan_eval", description="Score Indic-Translate translations and Indic-Speak audio; flag errors with evidence.")
    p.add_argument("--dataset", required=True, type=Path, help="JSONL with id, source, lang (+ optional reference, translation, audio, labels)")
    p.add_argument("--langs", default="", help="comma-separated language codes to score (default: all in the dataset)")
    p.add_argument("--out", required=True, type=Path, help="report folder")
    p.add_argument("--generate", action="store_true", help="create missing translations and audio with Bodhan models")
    p.add_argument("--baseline", type=Path, help="previous report folder (or its items.jsonl) to diff against")
    p.add_argument("--calibration", type=Path, help="JSONL of {id, labels} used to measure and tune the harness")
    p.add_argument("--max-cost-inr", type=float, default=None, help="stop paid Bodhan calls before spending more than this")
    p.add_argument("--thresholds", type=Path, help="override thresholds/weights JSON (default: packaged thresholds.json)")
    p.add_argument("--speech-sample", type=int, default=None, help="max items to synthesize speech for with --generate")
    p.add_argument("--consistency-sample", type=int, default=30, help="items re-judged to measure judge consistency (0 = off)")
    p.add_argument("--max-verify", type=int, default=300, help="max uncertain clusters sent to the LLM verifier")
    p.add_argument("--no-llm", action="store_true", help="skip the LLM judge and verifier (deterministic + model round-trip checks only)")
    p.add_argument("--concurrency", type=int, default=8, help="items processed concurrently")
    p.add_argument("--cache-dir", type=Path, default=None, help="cache folder (default .bodhan_eval_cache or $BODHAN_EVAL_CACHE)")
    p.add_argument("--fail-on-regression", type=int, default=None, metavar="N",
                   help="CI mode: exit 3 if more than N new major/critical flags vs --baseline")
    p.add_argument("--review-queue", type=int, default=40, help="rows in review_queue.csv")
    p.add_argument("--freeze-thresholds", action="store_true",
                   help="use the thresholds as given; measure against labels but do not re-tune (automatic with --baseline)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    jl = JsonLines(args.out / "logs.jsonl")
    root.addHandler(jl)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO if args.verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root.addHandler(console)
    for noisy in ("httpx", "httpx2", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not args.dataset.exists():
        print(f"dataset not found: {args.dataset}", file=sys.stderr)
        return 2
    settings = Settings.from_env(args.cache_dir)
    langs = parse_langs(args.langs)
    freeze = False
    if args.baseline and not args.thresholds:
        # Comparing two system versions needs a fixed measuring instrument: reuse the baseline's thresholds.
        base_dir = args.baseline if args.baseline.is_dir() else args.baseline.parent
        if (base_dir / "thresholds_tuned.json").exists():
            args.thresholds, freeze = base_dir / "thresholds_tuned.json", True
    opts = Options(dataset=args.dataset, out=args.out, langs=langs, generate=args.generate, baseline=args.baseline,
                   calibration=args.calibration, max_cost_inr=args.max_cost_inr, thresholds=args.thresholds,
                   speech_sample=args.speech_sample, consistency_sample=args.consistency_sample, max_verify=args.max_verify,
                   no_llm=args.no_llm, item_concurrency=args.concurrency, fail_on_regression=args.fail_on_regression,
                   review_queue=args.review_queue, freeze_thresholds=freeze or args.freeze_thresholds)
    if not settings.llm_enabled and not args.no_llm:
        print("note: LLM_BASE_URL / LLM_MODEL / LLM_API_KEY not set; running without the LLM judge", file=sys.stderr)
    t0 = time.time()
    run = Run(opts, settings)
    result = asyncio.run(run.execute())
    items, _ = load_dataset(args.dataset, langs or None)
    if args.calibration:
        from .dataset import load_labels
        ext = load_labels(args.calibration)
        for it in items:
            if it.id in ext:
                it.labels = ext[it.id]
    rep = write_report(args.out, result, {it.id: it for it in items}, args.baseline, args.review_queue)
    meta_path = args.out / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["wall_time_s"] = round(time.time() - t0, 1)
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    run.cache.close()
    jl.close()
    s = rep["summary"]
    print(f"scored {s['items_scored']} items: {s['status_counts'].get('flagged', 0)} flagged, {s['status_counts'].get('clean', 0)} clean "
          f"-> {args.out / 'summary.md'}  (spent ₹{meta['spent_total_inr']:.3f} this run, {meta['wall_time_s']}s)")
    if args.fail_on_regression is not None and rep["diff"]:
        bad = rep["diff"]["counts"]["new_major_or_worse"]
        if bad > args.fail_on_regression:
            print(f"CI: {bad} new major/critical flags > {args.fail_on_regression}", file=sys.stderr)
            return 3
    return 0
