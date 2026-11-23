"""End-to-end evaluation pipeline.

Stages per record (each optional stage is switched on by a CLI flag):

1. ``--generate``        translate the source with Indic-Translate when the
                         record has no translation
2. deterministic checks  always
3. ``--back-translate``  Indic -> English with Indic-Translate, compared with
                         the English source (reference-free)
4. ``--speech``          Indic-Speak TTS -> Indic-Transcribe STT, plus audio
                         quality checks (first ``--speech-limit`` records)
5. ``--llm-judge``       local Ollama judge (review-only findings)

Then: report, optional calibration, optional baseline comparison.

A failure in one stage of one record is recorded in ``item["errors"]`` and
the run continues. Paid calls go through the ``--max-cost-inr`` budget guard;
cache hits are free.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bodhan_eval.baseline import compare_to_baseline, load_baseline
from bodhan_eval.cache import JsonCache
from bodhan_eval.calibration import evaluate_injected_cases
from bodhan_eval.checks import run_deterministic_checks
from bodhan_eval.cost import BudgetExceededError, CostTracker
from bodhan_eval.dataset import load_dataset
from bodhan_eval.llm_judge import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    PROMPT_HASH,
    PROMPT_VERSION,
    JudgeUnavailableError,
    findings_to_checks,
)
from bodhan_eval.ratelimit import RateLimiter
from bodhan_eval.report import build_report, save_markdown_report, save_report
from bodhan_eval.round_trip import evaluate_round_trip
from bodhan_eval.translation_service import CachedTranslator


@dataclass
class EvalConfig:
    dataset: str
    languages: list[str]
    out: str = "outputs"
    calibration: str | None = None
    back_translate: bool = False
    speech: bool = False
    speech_limit: int | None = None
    llm_judge: bool = False
    generate: bool = False
    baseline: str | None = None
    max_cost_inr: float | None = 50.0
    cache_dir: str | None = None
    voice: str | None = None
    judge_model: str = DEFAULT_MODEL
    ollama_url: str = DEFAULT_BASE_URL
    judge_timeout: int = 120
    request_interval: float = 1.0
    extra: dict = field(default_factory=dict)


def load_calibration_cases(path: str) -> list[dict]:
    """Load injected, labelled calibration cases from JSONL."""
    cases = []

    with Path(path).open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid calibration JSON on line {line_number}"
                ) from exc

            required = {
                "id",
                "clean_translation",
                "mutated_text",
                "injected_category",
            }

            missing = required - case.keys()
            if missing:
                raise ValueError(
                    f"Calibration line {line_number} missing: {sorted(missing)}"
                )

            cases.append(case)

    return cases


def select_speech_records(records: list[dict], limit: int | None) -> set[str]:
    """Pick up to ``limit`` records with translations, round-robin by language.

    With ``limit`` equal to the number of languages this is one record per
    language (the original behaviour). ``None`` selects every record.
    """
    eligible = [r for r in records if r.get("translation")]
    if limit is None:
        return {r["id"] for r in eligible}

    by_language = defaultdict(list)
    order = []
    for record in eligible:
        if record["language"] not in by_language:
            order.append(record["language"])
        by_language[record["language"]].append(record)

    selected = []
    while len(selected) < limit and any(by_language[lang] for lang in order):
        for language in order:
            if by_language[language] and len(selected) < limit:
                selected.append(by_language[language].pop(0)["id"])

    return set(selected)


def _error(stage: str, exc: Exception) -> dict:
    return {
        "stage": stage,
        "type": "budget" if isinstance(exc, BudgetExceededError) else type(exc).__name__,
        "message": str(exc),
    }


def run_evaluation(
    config: EvalConfig,
    translate_client=None,
    speech_client=None,
    judge=None,
) -> dict:
    """Run the pipeline. Clients may be injected (tests use fakes)."""
    output_dir = Path(config.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(config.cache_dir) if config.cache_dir else output_dir

    records = load_dataset(config.dataset, config.languages)
    cost = CostTracker(max_inr=config.max_cost_inr)

    # -- clients (created lazily: offline runs need no keys) --------------
    translator = None
    if config.generate or config.back_translate:
        if translate_client is None:
            from bodhan_eval.bodhan import BodhanClient
            translate_client = BodhanClient()
        translator = CachedTranslator(
            translate_client,
            JsonCache(cache_dir / "translation_cache.json"),
            cost,
            RateLimiter("indic-translate", min_interval=config.request_interval),
        )

    speech_evaluator = None
    speech_ids: set[str] = set()
    if config.speech:
        from bodhan_eval.speech_service import SpeechEvaluator
        if speech_client is None:
            from bodhan_eval.speech import BodhanSpeechClient
            speech_client = BodhanSpeechClient()
        speech_evaluator = SpeechEvaluator(
            speech_client, cache_dir, cost, config.voice,
            RateLimiter("speech", min_interval=config.request_interval),
        )

    judge_state = {"enabled": False, "status": "disabled", "error": None}
    if config.llm_judge:
        if judge is None:
            from bodhan_eval.llm_judge import OllamaJudge
            judge = OllamaJudge(
                model=config.judge_model,
                base_url=config.ollama_url,
                timeout=config.judge_timeout,
                cache_path=cache_dir / "llm_judge_cache.json",
            )
        try:
            judge.check_available()
            judge_state.update(enabled=True, status="available")
        except JudgeUnavailableError as exc:
            judge_state.update(status="unavailable", error=str(exc))
            print(f"Warning: LLM judge disabled: {exc}")

    # -- stage 1: generation ----------------------------------------------
    generation = Counter()
    pre_errors: dict[str, list] = defaultdict(list)
    for record in records:
        if record.get("translation"):
            record.setdefault("translation_source", "dataset")
            continue
        if not (translator and config.generate):
            continue
        try:
            result = translator.translate(record["source_text"], record["language"])
            record["translation"] = result["translation"].strip()
            record["translation_source"] = "bodhan_generated"
            generation["cached" if result["cached"] else "generated"] += 1
            if result.get("truncated"):
                pre_errors[record["id"]].append({
                    "stage": "generate", "type": "truncated",
                    "message": "Generated translation hit the output length limit.",
                })
        except Exception as exc:  # noqa: BLE001 - recorded per item
            generation["failed"] += 1
            pre_errors[record["id"]].append(_error("generate", exc))

    if config.generate:
        # Save the dataset with generated translations so it can be reused
        # (for example as calibration material) without re-translating.
        # These translations are *candidates*, never references.
        with (output_dir / "generated_dataset.jsonl").open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")

    if speech_evaluator:
        speech_ids = select_speech_records(records, config.speech_limit)

    # -- per-record evaluation ----------------------------------------------
    results = []
    for index, record in enumerate(records, start=1):
        item = {
            "id": record["id"],
            "language": record["language"],
            "translation_source": record.get("translation_source"),
            "checks": run_deterministic_checks(record),
        }
        errors = list(pre_errors.get(record["id"], []))

        if not record.get("translation"):
            item["status"] = "not_evaluated"
            errors.append({
                "stage": "dataset", "type": "missing_translation",
                "message": "No translation (pass --generate to create one).",
            })

        if translator and config.back_translate and record.get("translation"):
            try:
                round_trip = evaluate_round_trip(
                    translator, record["source_text"], record["translation"]
                )
                item["round_trip"] = round_trip
                item["checks"].extend(round_trip["checks"])
            except Exception as exc:  # noqa: BLE001
                errors.append(_error("back_translate", exc))

        if speech_evaluator and record["id"] in speech_ids:
            try:
                speech = speech_evaluator.evaluate(record)
                item["speech"] = speech["speech"]
                item["checks"].extend(speech["checks"])
            except Exception as exc:  # noqa: BLE001
                errors.append(_error("speech", exc))

        if judge_state["enabled"] and record.get("translation"):
            outcome = judge.judge(record["source_text"], record["translation"], record["language"])
            item["llm_judge"] = outcome.to_dict()
            item["checks"].extend(findings_to_checks(outcome))
            if outcome.status == "unavailable":
                # Do not wait for a timeout on every remaining record.
                judge_state.update(enabled=False, status="unavailable", error=outcome.error)
                print(f"Warning: LLM judge became unavailable: {outcome.error}")
            elif outcome.status == "error":
                errors.append({"stage": "llm_judge", "type": "invalid_response",
                               "message": outcome.error})

        if errors:
            item["errors"] = errors
        results.append(item)

        if index % 25 == 0:
            print(f"Evaluated {index}/{len(records)} records")

    # -- report --------------------------------------------------------------
    report = build_report(results)
    report["run"] = {
        "config": _public_config(config),
        "generation": dict(generation),
        "cost": cost.summary(),
        "caches": _cache_stats(translator, speech_evaluator, judge if config.llm_judge else None),
        "rate_limits": {
            name: service.limiter.stats()
            for name, service in [("translate", translator), ("speech", speech_evaluator)]
            if service is not None
        },
        "llm_judge": _judge_summary(results, judge_state, config) if config.llm_judge else None,
        "back_translation": _round_trip_summary(results) if config.back_translate else None,
        "speech": _speech_summary(results, speech_ids) if config.speech else None,
        "errors": _error_summary(results),
    }

    calibration_result = None
    if config.calibration:
        cases = load_calibration_cases(config.calibration)
        calibration_result = evaluate_injected_cases(
            cases, judge=judge if judge_state["enabled"] else None
        )
        (output_dir / "calibration.json").write_text(
            json.dumps(calibration_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    baseline_result = None
    if config.baseline:
        baseline_result = compare_to_baseline(report, load_baseline(config.baseline))
        report["baseline_comparison"] = baseline_result
        (output_dir / "baseline_comparison.json").write_text(
            json.dumps(baseline_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    report_file = save_report(report, str(output_dir))

    items_file = output_dir / "items.jsonl"
    with items_file.open("w", encoding="utf-8") as file:
        for item in results:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    markdown_file = save_markdown_report(report, str(output_dir), calibration_result)

    return {
        "report": report,
        "calibration": calibration_result,
        "baseline": baseline_result,
        "paths": {
            "summary_json": str(report_file),
            "summary_md": str(markdown_file),
            "items": str(items_file),
        },
    }


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def _public_config(config: EvalConfig) -> dict:
    """The run configuration, safe to publish (no keys or secrets)."""
    data = asdict(config)
    data.pop("extra", None)
    data["judge_prompt_version"] = PROMPT_VERSION if config.llm_judge else None
    data["judge_prompt_hash"] = PROMPT_HASH if config.llm_judge else None
    return data


def _cache_stats(translator, speech_evaluator, judge) -> dict:
    stats = {}
    if translator:
        stats["translation"] = translator.cache.stats()
    if speech_evaluator:
        stats["speech"] = speech_evaluator.cache.stats()
    if judge is not None and hasattr(judge, "cache"):
        stats["llm_judge"] = {**judge.cache.stats(), "inference_calls": getattr(judge, "inference_calls", None)}
    return stats


def _judge_summary(results, judge_state, config) -> dict:
    statuses = Counter()
    categories = Counter()
    severities = Counter()
    rejected = Counter()
    cached = 0

    for item in results:
        outcome = item.get("llm_judge")
        if not outcome:
            continue
        statuses[outcome["status"]] += 1
        cached += bool(outcome.get("cached"))
        for finding in outcome["findings"]:
            categories[finding["category"]] += 1
            severities[finding["severity"]] += 1
        for rejection in outcome["rejected"]:
            rejected[rejection["reason"].split(":")[0]] += 1

    return {
        "model": config.judge_model,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
        "availability": judge_state["status"],
        "availability_error": judge_state["error"],
        "items_judged": sum(statuses.values()),
        "status_counts": dict(statuses),
        "cached_results": cached,
        "items_with_findings": sum(1 for i in results if (i.get("llm_judge") or {}).get("findings")),
        "findings_by_category": dict(categories),
        "findings_by_severity": dict(severities),
        "rejected_findings": dict(rejected),
    }


def _round_trip_summary(results) -> dict:
    scores = []
    flagged = 0
    for item in results:
        for check in item.get("round_trip", {}).get("checks", []):
            if check["category"] == "round_trip_similarity":
                scores.append(check["evidence"]["chrf"])
                flagged += check["status"] != "pass"
    return {
        "items": len(scores),
        "mean_chrf": round(sum(scores) / len(scores), 4) if scores else None,
        "min_chrf": min(scores) if scores else None,
        "flagged_for_review": flagged,
    }


def _speech_summary(results, speech_ids) -> dict:
    speech_items = [i for i in results if "speech" in i]
    statuses = defaultdict(Counter)
    for item in speech_items:
        for check in item["checks"]:
            if check["category"].startswith(("speech_", "audio_")):
                statuses[check["category"]][check.get("status") or (
                    "pass" if check.get("passed") else "fail" if check.get("passed") is False else "review"
                )] += 1
    return {
        "selected": len(speech_ids),
        "evaluated": len(speech_items),
        "cache_hits": sum(1 for i in speech_items if i["speech"].get("cached")),
        "check_status": {k: dict(v) for k, v in sorted(statuses.items())},
    }


def _error_summary(results) -> dict:
    counter = Counter()
    for item in results:
        for error in item.get("errors", []):
            counter[f"{error['stage']}:{error['type']}"] += 1
    return dict(counter)
