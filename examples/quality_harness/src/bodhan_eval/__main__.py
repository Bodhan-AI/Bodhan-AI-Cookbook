"""CLI entrypoint.

    python -m bodhan_eval --dataset data.jsonl --langs hi,ta,bn --out ./report
        [--generate] [--baseline ./prev] [--calibration labels.jsonl] [--max-cost-inr 50]

Extra flags (all optional): --concurrency --cache-dir --speech-sample-rate --judge-repeats
--no-judge --no-speech --fail-on-regression --seed --log-level --fake.

`--fake` uses deterministic offline clients (no keys, no network) for demos and tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

from . import dashboard
from .cache import Cache
from .calibrate import ItemEvidence, evaluate, fit_thresholds, thresholds_to_json
from .clients.bodhan import build_bodhan_clients
from .clients.fake import build_fake_clients
from .concurrency import ConcurrencyLimiter
from .config import load_api_config, load_languages, load_thresholds
from .cost import CostLedger
from .dataset import load_dataset
from .diff import compute_diff, has_regression, write_diff_md
from .judge import OpenAIJudge
from .logging_utils import StructuredLogger
from .pipeline import RunConfig, RunState, run_pipeline
from .report import build_meta, write_dashboard_data, write_reports


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bodhan_eval", description="Translation & speech quality harness")
    p.add_argument("--dataset", required=True)
    p.add_argument("--langs", required=True, help="comma-separated language codes, e.g. hi,ta,bn")
    p.add_argument("--out", required=True)
    p.add_argument("--generate", action="store_true")
    p.add_argument("--baseline", default=None)
    p.add_argument("--calibration", default=None)
    p.add_argument("--max-cost-inr", type=float, default=None)
    p.add_argument("--judge-input-inr-per-million", type=float, default=None)
    p.add_argument("--judge-output-inr-per-million", type=float, default=None)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--cache-dir", default=".cache")
    p.add_argument("--speech-sample-rate", type=float, default=0.25)
    p.add_argument("--judge-repeats", type=int, default=1)
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--no-speech", action="store_true")
    p.add_argument("--fail-on-regression", action="store_true")
    p.add_argument("--regression-threshold", type=int, default=1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--log-level", default="info")
    p.add_argument("--fake", action="store_true", help="deterministic offline clients (no keys)")
    p.add_argument("--no-dashboard", action="store_true", help="skip writing report/dashboard.html")
    p.add_argument("--thresholds", default=None,
                   help="thresholds JSON to load (default: config/thresholds.json). Calibration "
                        "writes fitted values to <out>/thresholds.fitted.json; pass it here to apply.")
    return p


def _attach_calibration_labels(items_by_id: dict, calibration_path: str | None) -> set[str] | None:
    if not calibration_path:
        return None
    from .models import Label

    attached: set[str] = set()
    with open(calibration_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            item = items_by_id.get(str(obj.get("id")))
            if item is not None and isinstance(obj.get("labels"), list):
                item.labels = [Label.from_dict(label) for label in obj["labels"]]
                item.raw["labels"] = obj["labels"]
                attached.add(item.id)
    return attached


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    rates = (args.judge_input_inr_per_million, args.judge_output_inr_per_million)
    if ((rates[0] is None) != (rates[1] is None)
            or any(r is not None and (not math.isfinite(r) or r < 0) for r in rates)):
        print("ERROR: supply both non-negative judge pricing rates, or neither.", file=sys.stderr)
        return 2
    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    out_dir = Path(args.out)
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)
    logger = StructuredLogger(out_dir / "logs" / "run.jsonl", level=args.log_level)

    cfg = RunConfig(
        dataset=args.dataset, langs=langs, out=args.out, generate=args.generate,
        baseline=args.baseline, calibration=args.calibration, max_cost_inr=args.max_cost_inr,
        judge_input_inr_per_million=args.judge_input_inr_per_million,
        judge_output_inr_per_million=args.judge_output_inr_per_million,
        concurrency=args.concurrency, cache_dir=args.cache_dir,
        speech_sample_rate=args.speech_sample_rate, judge_repeats=args.judge_repeats,
        no_judge=args.no_judge, no_speech=args.no_speech, seed=args.seed, log_level=args.log_level,
    )

    cache = Cache(Path(args.cache_dir))
    ledger = CostLedger(max_inr=args.max_cost_inr)
    limiter = ConcurrencyLimiter(args.concurrency)
    languages = load_languages()
    thresholds = load_thresholds(Path(args.thresholds) if args.thresholds else None)

    # clients
    if args.fake:
        # --fake replaces only the Bodhan (translate/speak/transcribe) clients with deterministic
        # offline ones. The LLM judge is still real when LLM_* keys are set (and not --no-judge),
        # so the judge can be exercised even when the Bodhan API is unreachable.
        rep = load_dataset(args.dataset, langs)
        gold = {it.translation: it.source for it in rep.items if it.translation}
        clients = build_fake_clients(cache, ledger, gold)
        for c in (clients.translate, clients.speak, clients.transcribe):
            c.ledger = ledger  # type: ignore[attr-defined]
        judge = _build_judge(cache, limiter, logger, args, ledger)
    else:
        api_cfg = load_api_config()
        try:
            clients = build_bodhan_clients(api_cfg, cache, ledger, limiter, logger, dict(os.environ))
        except RuntimeError as exc:
            logger.error("missing_keys", error=str(exc))
            print(f"ERROR: {exc}. Set Bodhan keys (see .env.example) or pass --fake.",
                  file=sys.stderr)
            return 2
        judge = _build_judge(cache, limiter, logger, args, ledger)

    started = time.time()
    state: RunState = asyncio.run(run_pipeline(cfg, clients, judge, cache, logger, thresholds,
                                               languages))

    # calibration (if any labels present)
    items_by_id = {it.id: it for it in state.load_report.items} if state.load_report else {}
    external_ids = _attach_calibration_labels(items_by_id, args.calibration)
    pairs = [ItemEvidence(item=items_by_id[i], result=state.results[i])
             for i in sorted(state.results) if i in items_by_id
             and (external_ids is None or i in external_ids)]
    labelled = [p for p in pairs if "labels" in p.item.raw]
    calibration = calib_notes = None
    if labelled:
        calibration = evaluate(pairs)
        fitted, calib_notes = fit_thresholds(pairs, thresholds)
        # Write fitted thresholds into the REPORT dir, never the committed defaults, so that
        # (a) reruns of the same command stay byte-identical and (b) defaults never get tuned to
        # a dataset. Apply them on a later run with --thresholds <out>/thresholds.fitted.json.
        fitted_path = out_dir / "thresholds.fitted.json"
        fitted_path.write_text(json.dumps(thresholds_to_json(fitted, fitted.low_support),
                                          ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        calib_notes = dict(calib_notes or {})
        calib_notes["fitted_thresholds_path"] = "thresholds.fitted.json"  # relative -> deterministic
        logger.info("calibration_done", labelled=len(labelled),
                    f1=(calibration.get("cells", {}).get("_all|_all", {}) or {}).get("f1"))

    meta = build_meta(cfg, started, time.time(), state)
    write_reports(args.out, state, cfg, calibration, calib_notes, meta)

    exit_code = 0
    diff_data = None
    if args.baseline:
        results = [state.results[k] for k in sorted(state.results)]
        diff_data = compute_diff(args.baseline, results)
        write_diff_md(str(out_dir / "diff.md"), diff_data)
        logger.info("diff_done", regressions=diff_data["regression_count"],
                    improvements=diff_data["improvement_count"])
        if args.fail_on_regression and has_regression(diff_data, args.regression_threshold):
            exit_code = 3

    if not args.no_dashboard:
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        write_dashboard_data(args.out, state, summary, calibration, diff_data)
        dashboard.build_from_report(args.out)
        logger.info("dashboard_written", path=str(out_dir / "dashboard.html"))

    logger.info("run_complete", items=len(state.results), exit_code=exit_code)
    logger.close()
    print(f"Report written to {out_dir}/ (summary.md, summary.json, items.jsonl, "
          f"calibration.json{', diff.md' if args.baseline else ''}"
          f"{'' if args.no_dashboard else ', dashboard.html'})")
    return exit_code


def _build_judge(cache, limiter, logger, args, ledger):
    if args.no_judge:
        return None
    base = os.environ.get("LLM_BASE_URL")
    key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")
    if not (base and key and model):
        logger.warning("judge_disabled", reason="LLM_BASE_URL/LLM_MODEL/LLM_API_KEY not all set")
        return None
    if args.max_cost_inr is not None and args.judge_input_inr_per_million is None:
        logger.warning("judge_cost_unmetered", reason="judge rates were not supplied")
    return OpenAIJudge(base, key, model, cache, limiter, logger, seed=args.seed or 7,
                       ledger=ledger, input_rate=args.judge_input_inr_per_million,
                       output_rate=args.judge_output_inr_per_million)


if __name__ == "__main__":
    raise SystemExit(main())
