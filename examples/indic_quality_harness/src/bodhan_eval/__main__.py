"""Command-line entry point: ``python -m bodhan_eval --dataset ...``."""

import argparse
import sys

from bodhan_eval.llm_judge import DEFAULT_BASE_URL, DEFAULT_MODEL
from bodhan_eval.pipeline import EvalConfig, load_calibration_cases, run_evaluation

__all__ = ["main", "parse_args", "load_calibration_cases"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Indic Translation and Speech Quality Harness"
    )

    parser.add_argument("--dataset", required=True, help="JSONL with id, language, source_text[, translation]")
    parser.add_argument("--langs", default="hi,ta,bn", help="Comma-separated language codes")
    parser.add_argument("--out", default="outputs", help="Output directory")
    parser.add_argument("--calibration", help="JSONL of labelled injected-error cases")
    parser.add_argument("--back-translate", action="store_true",
                        help="Reference-free check: translate back to English with Indic-Translate")
    parser.add_argument("--generate", action="store_true",
                        help="Translate records that have no translation with Indic-Translate")
    parser.add_argument("--baseline", help="Previous summary.json (or run directory) to compare against")
    parser.add_argument("--speech", action="store_true",
                        help="Indic-Speak TTS -> Indic-Transcribe STT round trip")
    parser.add_argument("--speech-limit", type=int, default=None,
                        help="Max records for speech (round-robin across languages)")
    parser.add_argument("--voice", default=None,
                        help="Indic-Speak voice for all languages (default: per-language voice)")
    parser.add_argument("--llm-judge", action="store_true",
                        help="Add review-only findings from a local Ollama judge")
    parser.add_argument("--judge-model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--judge-timeout", type=int, default=120)
    parser.add_argument("--max-cost-inr", type=float, default=50.0,
                        help="Budget for paid Bodhan calls in this run (cache hits are free)")
    parser.add_argument("--request-interval", type=float, default=1.0,
                        help="Minimum seconds between paid Bodhan calls (429 avoidance)")
    parser.add_argument("--cache-dir", default=None,
                        help="Where caches and audio live (default: --out)")

    args = parser.parse_args(argv)

    if args.speech_limit is not None and args.speech_limit < 1:
        parser.error("--speech-limit must be a positive integer")
    if args.max_cost_inr < 0:
        parser.error("--max-cost-inr cannot be negative")

    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    config = EvalConfig(
        dataset=args.dataset,
        languages=[lang.strip() for lang in args.langs.split(",") if lang.strip()],
        out=args.out,
        calibration=args.calibration,
        back_translate=args.back_translate,
        speech=args.speech,
        speech_limit=args.speech_limit,
        llm_judge=args.llm_judge,
        generate=args.generate,
        baseline=args.baseline,
        max_cost_inr=args.max_cost_inr,
        cache_dir=args.cache_dir,
        voice=args.voice,
        judge_model=args.judge_model,
        ollama_url=args.ollama_url,
        judge_timeout=args.judge_timeout,
        request_interval=args.request_interval,
    )

    try:
        result = run_evaluation(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    report = result["report"]
    run = report["run"]

    print(f"Processed {report['total_items']} items")
    print(f"Failed checks: {report['failed_checks']}  Review checks: {report['review_checks']}")
    print(f"Estimated spend: ₹{run['cost']['estimated_spend_inr']} (budget ₹{run['cost']['max_inr']})")
    if run["cost"]["budget_exhausted"]:
        print(f"Warning: {run['cost']['blocked_calls']} paid call(s) skipped: budget reached")

    calibration = result["calibration"]
    if calibration:
        overall = calibration["metrics"]["overall"]
        print("Calibration recall:", overall["recall"])
        print("Calibration precision:", overall["precision"])

    if result["baseline"]:
        print(f"Baseline: {result['baseline']['regression_count']} regression(s), "
              f"{result['baseline']['fix_count']} fix(es)")

    print(f"Markdown report saved: {result['paths']['summary_md']}")
    print(f"Report saved: {result['paths']['summary_json']}")
    print(f"Item results saved: {result['paths']['items']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
