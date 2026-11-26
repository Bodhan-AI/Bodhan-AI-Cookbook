"""CLI:  python -m bodhan_eval --dataset data.jsonl --langs hi,ta,bn --out ./report [...]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .cache import Cache
from .clients import BodhanBackend, Services
from .cost import CostTracker
from .pipeline import Config, run
from .report import write_report


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bodhan_eval", description="Quality harness for Indic-Translate and Indic-Speak")
    p.add_argument("--dataset", required=True, help="JSONL dataset")
    p.add_argument("--langs", default="", help="comma-separated language codes (any code works); default: all in dataset")
    p.add_argument("--out", required=True, help="report directory")
    p.add_argument("--generate", action="store_true", help="create missing translations/audio with Bodhan models")
    p.add_argument("--baseline", help="previous report directory to diff against")
    p.add_argument("--calibration", help="JSONL of {id, labels} for calibration")
    p.add_argument("--max-cost-inr", type=float, default=50.0, help="hard cap on NEW Bodhan spend (cached calls are free)")
    p.add_argument("--cache-dir", default=".cache/bodhan_eval")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--judge-runs", type=int, default=3, help="LLM judge repetitions per item (consistency is measured from these)")
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--no-speech", action="store_true")
    p.add_argument("--no-bt", action="store_true", help="skip back-translation")
    p.add_argument("--speech-sample", type=float, default=0.25, help="fraction of unsuspicious items to synthesize with --generate")
    p.add_argument("--bodhan-interval", type=float, default=0.1, help="min seconds between Bodhan API calls (all threads); 429s are also retried with backoff")
    p.add_argument("--llm-interval", type=float, default=0.1, help="min seconds between judge LLM calls; use 6 for a ~10 calls/min free tier")
    p.add_argument("--holdout-frac", type=float, default=0.3, help="share of labelled items kept out of threshold tuning and used for the reported targets (0 disables; needs >=16 labelled items)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--ci", action="store_true", help="exit 2 if flagged-item rate regresses vs --baseline by more than --ci-tolerance")
    p.add_argument("--ci-tolerance", type=float, default=0.02)
    return p


def main(argv=None, backend=None) -> int:
    load_dotenv()
    a = build_parser().parse_args(argv)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(Path(a.out) / "run.log"), level=logging.INFO, format="%(asctime)s %(message)s", encoding="utf-8")
    cfg = Config(dataset=a.dataset, out=a.out, langs=[l.strip().lower() for l in a.langs.split(",") if l.strip()],
                 generate=a.generate, baseline=a.baseline, calibration=a.calibration, max_cost_inr=a.max_cost_inr,
                 cache_dir=a.cache_dir, concurrency=a.concurrency, judge_runs=max(1, a.judge_runs), no_judge=a.no_judge,
                 no_speech=a.no_speech, no_bt=a.no_bt, speech_sample=a.speech_sample, seed=a.seed, holdout_frac=a.holdout_frac, ci=a.ci, ci_tolerance=a.ci_tolerance)
    cost = CostTracker(cfg.max_cost_inr)
    svc = Services(backend or BodhanBackend(), Cache(cfg.cache_dir), cost, concurrency=cfg.concurrency,
                   intervals={"translate": a.bodhan_interval, "speak": a.bodhan_interval,
                              "transcribe": a.bodhan_interval, "llm": a.llm_interval} if backend is None else {})
    try:
        result = run(cfg, svc)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    budget_hit = any("budget" in e["error"].lower() for r in result["results"] for e in r["errors"])
    out = write_report(result, cfg, cost, budget_hit, a.baseline)
    s = out["summary"]
    print(f"evaluated {s['items_evaluated']}/{s['items_total']} items, {s['items_flagged']} flagged; "
          f"spent INR {cost.new_spend:.4f} this run (uncached equivalent INR {s['cost']['uncached_total_inr']}); report in {a.out}")
    if a.ci and out["diff"] and "error" not in out["diff"]:
        import json
        base = json.loads((Path(a.baseline) / "summary.json").read_text(encoding="utf-8"))
        rate = lambda x: x["items_flagged"] / max(1, x["items_evaluated"])
        if rate(s) - rate(base) > a.ci_tolerance:
            print("CI: flagged-item rate regressed beyond tolerance", file=sys.stderr)
            return 2
    return 0
