import argparse
from pathlib import Path
import json
import time

from .budget import Budget
from .cache import Cache
from .calibrate import evaluate_labeled_items, leave_one_language_out
from .client.bodhan import BodhanClient
from .client.llm import LLMClient
from .config import (
    BODHAN_SPEAK_KEY,
    BODHAN_TRANSCRIBE_KEY,
    BODHAN_TRANSLATE_KEY,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
)
from .dataset import read_dataset
from .diff import compare_runs, diff_markdown
from .generation import attach_generation_outcomes, generate_missing_translations
from .judge_stage import (
    has_judge_candidates,
    run_judge_consistency,
    run_judge_stage,
)
from .pipeline import run_pipeline
from .report import write_outputs
from .speech_stage import run_speech_stage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bodhan_eval",
        description="Deterministic translation and speech quality evaluation harness",
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--langs", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--max-cost-inr", type=float, default=50.0)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20261119)
    parser.add_argument("--span-ref", choices=("auto", "source", "translation"), default="auto")
    parser.add_argument("--speech-sample", type=int, default=30)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--judge-consistency-n", type=int, default=30)
    parser.add_argument("--no-speech", action="store_true")
    parser.add_argument("--no-judge", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    started_at = time.perf_counter()
    if not args.dataset.is_file():
        parser.error(f"dataset does not exist: {args.dataset}")
    if args.calibration is not None and not args.calibration.is_file():
        parser.error(f"calibration dataset does not exist: {args.calibration}")
    if args.max_cost_inr < 0:
        parser.error("--max-cost-inr must be non-negative")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.speech_sample < 0:
        parser.error("--speech-sample must be non-negative")
    langs = {code.strip() for code in args.langs.split(",") if code.strip()}
    result = read_dataset(args.dataset, langs)
    budget = Budget(max_inr=args.max_cost_inr)
    generation_outcomes = {}
    items_to_generate = [
        item for item in result.items
        if not item.translation or not item.translation.strip()
    ]
    if args.generate and items_to_generate:
        if not BODHAN_TRANSLATE_KEY:
            parser.error(
                "BODHAN_TRANSLATE_KEY is required for --generate when an item "
                "has no translation"
            )
        import asyncio

        cache_dir = args.cache_dir or args.out.parent / ".cache" / "bodhan_eval"

        async def generate() -> dict:
            async with BodhanClient(
                BODHAN_TRANSLATE_KEY,
                "",
                "",
                Cache(cache_dir),
                budget,
            ) as client:
                return await generate_missing_translations(result, client, budget)

        generation_outcomes = asyncio.run(generate())
    records = run_pipeline(result, budget)
    attach_generation_outcomes(records, generation_outcomes)
    asr_transcripts = {}
    judge_consistency = None
    speech_requested = any(item.audio is not None for item in result.items) or (
        args.generate
        and args.speech_sample > 0
        and any(item.translation for item in result.items)
    )
    if speech_requested:
        import asyncio

        cache_dir = args.cache_dir or args.out.parent / ".cache" / "bodhan_eval"
        speech_client_available = bool(BODHAN_SPEAK_KEY or BODHAN_TRANSCRIBE_KEY)

        async def speech() -> dict:
            if not speech_client_available:
                return await run_speech_stage(
                    result,
                    records,
                    None,
                    budget,
                    enabled=not args.no_speech,
                    generate=args.generate,
                    speech_sample=args.speech_sample,
                    speak_available=bool(BODHAN_SPEAK_KEY),
                    transcribe_available=bool(BODHAN_TRANSCRIBE_KEY),
                )
            async with BodhanClient(
                BODHAN_TRANSLATE_KEY or "",
                BODHAN_SPEAK_KEY or "",
                BODHAN_TRANSCRIBE_KEY or "",
                Cache(cache_dir),
                budget,
            ) as client:
                return await run_speech_stage(
                    result,
                    records,
                    client,
                    budget,
                    enabled=not args.no_speech,
                    generate=args.generate,
                    speech_sample=args.speech_sample,
                    speak_available=bool(BODHAN_SPEAK_KEY),
                    transcribe_available=bool(BODHAN_TRANSCRIBE_KEY),
                )

        asr_transcripts = asyncio.run(speech())
    if has_judge_candidates(records):
        import asyncio

        cache_dir = args.cache_dir or args.out.parent / ".cache" / "bodhan_eval"
        llm_configured = bool(LLM_BASE_URL and LLM_MODEL and LLM_API_KEY)
        if args.no_judge or not llm_configured:
            asyncio.run(run_judge_stage(
                result.items,
                records,
                None,
                enabled=not args.no_judge,
            ))
        else:
            async def judge() -> None:
                nonlocal judge_consistency
                judge_cache = Cache(cache_dir)
                async with LLMClient(
                    LLM_BASE_URL or "",
                    LLM_MODEL or "",
                    LLM_API_KEY or "",
                    judge_cache,
                ) as client:
                    await run_judge_stage(
                        result.items,
                        records,
                        client,
                        enabled=True,
                        cache=judge_cache,
                        model=LLM_MODEL or "",
                        base_url=LLM_BASE_URL or "",
                        asr_transcripts=asr_transcripts,
                    )
                    judge_consistency = await run_judge_consistency(
                        result.items,
                        records,
                        client,
                        judge_cache,
                        model=LLM_MODEL or "",
                        base_url=LLM_BASE_URL or "",
                        sample_size=args.judge_consistency_n,
                        runs=3,
                    )

            asyncio.run(judge())
    calibration_result = None
    if args.calibration is not None:
        calibration_data = read_dataset(args.calibration, langs)
        run_keys = {
            (record["id"], record["lang"])
            for record in records
            if not record.get("quarantined", False)
        }
        calibration_keys = {
            (item.id, item.lang)
            for item in calibration_data.items
        }
        unmatched = calibration_keys - run_keys
        if unmatched:
            parser.error(
                "--calibration items must also be present in --dataset "
                f"(unmatched id/lang pairs: {len(unmatched)}); run the harness "
                "on the labeled dataset or provide labels for matching items"
            )
        prediction_map = {
            (record["id"], record["lang"]): record["flags"]
            for record in records
        }
        calibration_result = evaluate_labeled_items(
            calibration_data.items,
            prediction_map,
            seed=args.seed,
        )
        labeled_languages = {item.lang for item in calibration_data.items}
        if labeled_languages:
            calibration_result["generalization"] = {
                "leave_one_language_out": leave_one_language_out(
                    calibration_data.items,
                    prediction_map,
                    seed=args.seed,
                ),
                "unseen_languages": {
                    lang: {
                        "status": "insufficient_support",
                        "reason": "no labeled held-out items",
                        "threshold_origin": "defaults_not_tuned",
                    }
                    for lang in sorted(langs - labeled_languages)
                },
            }
    write_outputs(
        args.out,
        records,
        budget,
        input_lines=result.total_input_lines,
        seed=args.seed,
        calibration_result=calibration_result,
        judge_consistency=judge_consistency,
    )
    run_meta = {
        "seed": args.seed,
        "langs": sorted(langs),
        "generate": args.generate,
        "budget_limit_inr": args.max_cost_inr,
        "cost_ledger": budget.ledger,
        "budget_skips": budget.skips,
    }
    (args.out / "run_meta.json").write_text(
        json.dumps(run_meta, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    regression = False
    if args.baseline:
        comparison = compare_runs(args.baseline, args.out, seed=args.seed)
        regression = comparison.regression
        (args.out / "diff.md").write_text(
            diff_markdown(args.baseline, args.out, seed=args.seed),
            encoding="utf-8",
            newline="\n",
        )
    summary = json.loads((args.out / "summary.json").read_text(encoding="utf-8"))
    runtime = time.perf_counter() - started_at
    print(
        f"items={summary['items']} coverage={summary['coverage']['overall']:.6f} "
        f"flags={summary['flags']} cost_inr={summary['cost_inr']:.6f} "
        f"runtime_s={runtime:.3f}"
    )
    return 2 if args.fail_on_regression and regression else 0
