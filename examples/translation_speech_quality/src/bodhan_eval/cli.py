import argparse
import json
import math
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from .checks import SCRIPTS
from .providers import Ledger, ModelClient
from .reports import dump, finalize
from .runner import read_dataset, run_items


def parser():
    p = argparse.ArgumentParser(description="Evaluate supplied or generated Indic translation and speech")
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--langs", default="hi,ta,bn")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--generate", action="store_true")
    p.add_argument("--baseline", type=Path)
    p.add_argument("--calibration", type=Path)
    p.add_argument("--max-cost-inr", type=float, default=50)
    p.add_argument("--cache", type=Path, default=Path(".cache/model_calls"))
    p.add_argument("--offline", action="store_true", help="Read cached calls only; no model network requests")
    p.add_argument("--no-judge", action="store_true", help="Run a documented deterministic/Bodhan subset")
    p.add_argument("--no-roundtrips", action="store_true")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--speech-sample", type=int, default=12, help="Number of missing audio outputs to generate; supplied audio is always checked")
    p.add_argument("--judge-repeat-sample", type=int, default=6)
    p.add_argument("--llm-input-inr-per-million", type=float)
    p.add_argument("--llm-output-inr-per-million", type=float)
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--env-file", type=Path, help="Optional private environment file outside the repository")
    p.add_argument("--fail-on-regression", type=int, help="CI exits 2 when flagged items increase by more than this many")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    langs = args.langs.split(",")
    if any(lang not in SCRIPTS for lang in langs):
        p.error("--langs contains an unsupported language code")
    if not math.isfinite(args.max_cost_inr) or args.max_cost_inr < 0 or not 1 <= args.workers <= 8 or args.timeout <= 0 or not math.isfinite(args.timeout):
        p.error("Budget must be nonnegative, workers 1–8, timeout positive")
    if args.speech_sample < 0 or args.judge_repeat_sample < 0:
        p.error("Sampling counts must be nonnegative")
    if any(x is not None and (x < 0 or not math.isfinite(x)) for x in (args.llm_input_inr_per_million, args.llm_output_inr_per_million)):
        p.error("Token prices must be nonnegative")
    load_dotenv(args.env_file) if args.env_file else load_dotenv()
    # Provider-agnostic fallback estimates let the required interface run with
    # only the documented credentials. Explicit prices remain preferable.
    for name, fallback in (("llm_input_inr_per_million", 100), ("llm_output_inr_per_million", 1000)):
        if getattr(args, name) is None:
            try:
                price = float(os.getenv(name.upper(), str(fallback)))
            except ValueError:
                p.error("Invalid configured LLM token price")
            if price < 0 or not math.isfinite(price):
                p.error("Configured LLM prices must be finite and nonnegative")
            setattr(args, name, price)
    started = time.monotonic()
    items, invalid = read_dataset(args.dataset)
    items = [item for item in items if item["lang"] in langs]
    if not items:
        invalid.append({"status": "invalid", "reason": "No valid items match the requested languages"})
    label_records = [{**item, "annotation_source": "human"} for item in items
                     if "labels" in item and item.get("annotation_source", "human") == "human"]
    if args.calibration:
        try:
            label_records += [json.loads(line) for line in args.calibration.read_text().splitlines() if line.strip()]
        except (ValueError, OSError):
            p.error("Calibration file must be readable JSONL")
        if any(not isinstance(record, dict) or not isinstance(record.get("id"), str)
               or not isinstance(record.get("labels"), list)
               or any(not isinstance(label, dict) or label.get("type") not in (
                   "omission", "addition", "mistranslation", "number", "entity", "format",
                   "wrong_language", "speech_omission", "speech_repetition", "garbled_audio")
                      for label in record["labels"]) for record in label_records):
            p.error("Calibration file requires id and labels with supported error types")
    args.out.mkdir(parents=True, exist_ok=True)
    client = ModelClient(args.cache, Ledger(args.max_cost_inr), offline=args.offline,
                         concurrency=args.workers, timeout=args.timeout,
                         llm_input_price=args.llm_input_inr_per_million,
                         llm_output_price=args.llm_output_inr_per_million)
    rows = run_items(items, client=client, dataset_dir=args.dataset.resolve().parent, out=args.out,
                     generate=args.generate, roundtrips=not args.no_roundtrips,
                     judge=not args.no_judge, workers=args.workers,
                     speech_sample=args.speech_sample, judge_repeat_sample=args.judge_repeat_sample)
    summary = finalize(rows, items, label_records, invalid, args.out, args.baseline)
    execution = {"charged_or_reserved_inr": round(client.ledger.used, 6),
                 "budget_inr": args.max_cost_inr, "elapsed_seconds": round(time.monotonic() - started, 4),
                 "cache_hits": sum(bool(e.get("cache_hit")) for e in client.events),
                 "api_calls": sum(not e.get("cache_hit", False) for e in client.events),
                 "scope": {"judge": not args.no_judge, "roundtrips": not args.no_roundtrips,
                           "generated_speech_sample": args.speech_sample},
                 "judge_price_estimates_inr_per_million": {"input": args.llm_input_inr_per_million,
                                                          "output": args.llm_output_inr_per_million},
                 "billing_note": "Bodhan published prices; supplied judge prices. Unknown failed-call billing retains the upper reservation."}
    dump(args.out / "execution.json", execution)
    with (args.out / "trace.jsonl").open("w") as trace:
        for event in client.events:
            trace.write(json.dumps(event, sort_keys=True) + "\n")
    client.http.close()
    print(json.dumps({"status": summary["status"], "items": len(rows), **execution}, indent=2))
    if args.fail_on_regression is not None and args.baseline:
        baseline = json.loads((args.baseline / "summary.json").read_text())
        if summary["flagged_items"] - baseline["flagged_items"] > args.fail_on_regression:
            return 2
    return 1 if summary["status"] == "partial" else 0
