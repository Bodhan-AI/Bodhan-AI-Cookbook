import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

from bodhan_eval.bodhan_client import BodhanClient, BodhanBudgetExceeded
from bodhan_eval.evaluator import IndicEvalEvaluator
from bodhan_eval.calibration import CalibrationEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


# =========================================================
# LOGGING
# =========================================================

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# =========================================================
# DATASET UTILITIES
# =========================================================

def load_jsonl(file_path):
    records = []

    with open(file_path, "r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):

            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON at line {line_number}: {exc}"
                ) from exc

            if not isinstance(record, dict):
                raise ValueError(
                    f"Expected a JSON object at line {line_number}"
                )

            required_fields = ["id", "source", "lang"]

            missing_fields = [
                field for field in required_fields
                if field not in record
            ]

            if missing_fields:
                raise ValueError(
                    f"Line {line_number} is missing fields: "
                    f"{missing_fields}"
                )

            records.append(record)

    return records


def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
            default=str,
        )


def save_jsonl(file_path, records):
    with open(file_path, "w", encoding="utf-8") as file:
        for record in records:
            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    default=str,
                ) + "\n"
            )


# =========================================================
# GENERATE MISSING TRANSLATIONS AND AUDIO
# =========================================================

def generate_missing_outputs(records, client, output_dir):

    generated_translations = 0
    generated_audio = 0

    audio_dir = output_dir / "audio"

    for record in records:

        target_lang = record.get("target_lang")

        # Generate missing translation
        if not record.get("translation"):

            if not target_lang:
                logging.warning(
                    "Cannot generate translation for %s: "
                    "target_lang is missing.",
                    record["id"],
                )
                continue

            logging.info(
                "Generating translation for item %s",
                record["id"],
            )

            result = client.translate(
                text=record["source"],
                target_language=target_lang,
            )

            record["translation"] = result["translation"]
            record["translation_generated"] = True

            generated_translations += 1

        # Generate missing speech audio
        if not record.get("audio") and record.get("translation"):

            if not target_lang:
                logging.warning(
                    "Cannot generate audio for %s: "
                    "target_lang is missing.",
                    record["id"],
                )
                continue

            audio_dir.mkdir(parents=True, exist_ok=True)

            audio_path = audio_dir / f"{record['id']}.wav"

            logging.info(
                "Generating speech for item %s",
                record["id"],
            )

            client.speak(
                text=record["translation"],
                language=target_lang,
                output_path=str(audio_path),
            )

            if audio_path.exists():
                record["audio"] = str(audio_path)
                record["audio_generated"] = True

                generated_audio += 1

    logging.info(
        "Generated translations: %d",
        generated_translations,
    )

    logging.info(
        "Generated audio files: %d",
        generated_audio,
    )

    return records


# =========================================================
# SUMMARY GENERATION
# =========================================================

def create_summary(
    results, output_dir, selected_languages,
    usage_report=None, llm_usage_report=None
):

    total = len(results)

    passed = 0
    failed = 0
    warning = 0

    language_summary = {}

    for result in results:

        status = result.get(
            "status",
            result.get("overall_status", "unknown")
        )

        language = result.get("language", "unknown")

        if status == "passed":
            passed += 1
        elif status == "failed":
            failed += 1
        else:
            warning += 1

        if language not in language_summary:
            language_summary[language] = {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
            }

        language_summary[language]["total"] += 1

        if status in ["passed", "failed"]:
            language_summary[language][status] += 1
        else:
            language_summary[language]["warning"] += 1

    pass_rate = passed / total if total > 0 else 0

    summary = {
        "project": "IndicEval",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "languages": selected_languages,
        "total_items": total,
        "passed": passed,
        "failed": failed,
        "warning": warning,
        "pass_rate": round(pass_rate, 4),
        "language_summary": language_summary,
    }

    if usage_report is not None:
        summary["api_usage"] = usage_report

    if llm_usage_report is not None:
        summary["llm_usage"] = llm_usage_report

    bodhan_cost = (
        float(usage_report.get("estimated_cost_inr", 0.0))
        if usage_report is not None else 0.0
    )
    llm_cost = (
        float(llm_usage_report.get("estimated_cost_inr", 0.0))
        if llm_usage_report is not None else 0.0
    )
    summary["estimated_cost_inr"] = round(bodhan_cost + llm_cost, 8)

    bodhan_partial = (
        bool(usage_report.get("cost_is_partial", True))
        if usage_report is not None else True
    )
    llm_partial = (
        bool(llm_usage_report.get("cost_is_partial", True))
        if llm_usage_report is not None else True
    )
    summary["cost_tracking_partial"] = bodhan_partial or llm_partial

    save_json(output_dir / "summary.json", summary)

    markdown = [
        "# IndicEval Quality Report",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Overall Results",
        "",
        f"- Total items: {total}",
        f"- Passed: {passed}",
        f"- Failed: {failed}",
        f"- Warnings: {warning}",
        f"- Pass rate: {pass_rate:.2%}",
        "",
        "## API Usage and Estimated Cost",
        "",
    ]

    if usage_report is not None:
        markdown.extend([
            f"- Bodhan API requests: {sum(usage_report['requests'].values())}",
            f"- Bodhan cache hits: {sum(usage_report['cache_hits'].values())}",
            f"- Bodhan translation tokens: {usage_report['input_tokens']} input / "
            f"{usage_report['output_tokens']} output",
            f"- TTS characters: {usage_report['characters']['speak']}",
            f"- ASR audio minutes: "
            f"{usage_report['audio_minutes']['transcribe']:.4f}",
            f"- Estimated Bodhan cost: "
            f"₹{usage_report['estimated_cost_inr']:.6f}",
        ])

    if llm_usage_report is not None:
        markdown.extend([
            "",
            "### LLM Judge Usage",
            "",
            f"- Provider endpoint: {llm_usage_report.get('provider', 'unknown')}",
            f"- Model: {llm_usage_report.get('model', 'unknown')}",
            f"- Successful LLM requests with usage: {llm_usage_report.get('requests', 0)}",
            f"- LLM input tokens: {llm_usage_report.get('input_tokens', 0)}",
            f"- LLM output tokens: {llm_usage_report.get('output_tokens', 0)}",
            f"- Estimated LLM cost: ₹{llm_usage_report.get('estimated_cost_inr', 0.0):.8f}",
            f"- LLM cost tracking partial: {llm_usage_report.get('cost_is_partial', True)}",
        ])

    markdown.extend([
        "",
        f"- Combined estimated cost subtotal: ₹{summary['estimated_cost_inr']:.8f}",
        f"- Overall cost tracking partial: {summary['cost_tracking_partial']}",
        "",
        "> Costs are estimates based on configured provider rates. "
        "A partial flag means one or more rates or provider usage records are unavailable.",
        "> The --max-cost-inr guard currently applies to Bodhan API calls only; "
        "it does not cap external LLM judge charges.",
        "",
    ])

    markdown.extend([
        "## Language-wise Results",
        "",
        "| Language | Total | Passed | Failed | Warnings |",
        "|---|---:|---:|---:|---:|",
    ])

    for language, stats in language_summary.items():
        markdown.append(
            f"| {language} | "
            f"{stats['total']} | "
            f"{stats['passed']} | "
            f"{stats['failed']} | "
            f"{stats['warning']} |"
        )

    with open(
        output_dir / "summary.md",
        "w",
        encoding="utf-8",
    ) as file:
        file.write("\n".join(markdown))

    return summary


# =========================================================
# BASELINE COMPARISON
# =========================================================

def create_diff(current_summary, baseline_path, output_dir):

    diff_path = output_dir / "diff.md"

    if not baseline_path:
        diff_path.write_text(
            "# Baseline Comparison\n\n"
            "No baseline report was supplied.\n",
            encoding="utf-8",
        )
        return

    baseline_file = Path(baseline_path)

    if not baseline_file.exists():
        raise FileNotFoundError(
            f"Baseline report not found: {baseline_file}"
        )

    with open(
        baseline_file,
        "r",
        encoding="utf-8",
    ) as file:
        baseline = json.load(file)

    old_pass_rate = baseline.get("pass_rate", 0)
    new_pass_rate = current_summary.get("pass_rate", 0)

    difference = new_pass_rate - old_pass_rate

    report = [
        "# Baseline Comparison",
        "",
        "## Pass Rate",
        "",
        f"- Previous: {old_pass_rate:.2%}",
        f"- Current: {new_pass_rate:.2%}",
        f"- Difference: {difference:+.2%}",
        "",
    ]

    if difference > 0:
        report.append(
            "Quality improved compared with the baseline."
        )
    elif difference < 0:
        report.append(
            "Quality decreased compared with the baseline."
        )
    else:
        report.append(
            "No change in pass rate."
        )

    diff_path.write_text(
        "\n".join(report),
        encoding="utf-8",
    )




# =========================================================
# HUMAN-LABEL CALIBRATION
# =========================================================

CHECK_CATEGORY_MAP = {
    "number_preservation": "number_error",
    "repetition": "repetition",
    "markdown_integrity": "formatting_error",
    "latex_integrity": "formatting_error",
    "punctuation_integrity": "formatting_error",
    "omission_detection": "omission",
    "addition_detection": "addition",
    "entity_preservation": "terminology",
    "empty_translation": "omission",
    "length_anomaly": "omission",
}


def load_calibration_jsonl(file_path):
    """Load human labels. Each row must include an item id and label(s)."""

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Calibration file not found: {path}")

    records = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid calibration JSON at line {line_number}: {exc}"
                ) from exc

            if not isinstance(record, dict):
                raise ValueError(
                    f"Calibration line {line_number} must be a JSON object."
                )

            if not record.get("id"):
                raise ValueError(
                    f"Calibration line {line_number} is missing 'id'."
                )

            label_value = None
            for key in (
                "categories", "labels", "category", "label",
                "error_categories", "error_category", "ground_truth"
            ):
                if key in record:
                    label_value = record[key]
                    break

            if label_value is None:
                raise ValueError(
                    f"Calibration line {line_number} needs one of: "
                    "categories, labels, category, label, "
                    "error_categories, error_category, ground_truth."
                )

            if isinstance(label_value, str):
                labels = [label_value]
            elif isinstance(label_value, list):
                labels = label_value
            else:
                raise ValueError(
                    f"Calibration labels at line {line_number} "
                    "must be a string or list of strings."
                )

            record["_normalized_labels"] = (
                CalibrationEngine.normalize_predictions(labels)
            )
            records.append(record)

    if not records:
        raise ValueError("Calibration file contains no labelled records.")

    ids = [str(record["id"]) for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Calibration file contains duplicate item IDs.")

    return records


def _category_from_error(error):
    """Map an evaluator error object to a calibration category."""

    if not isinstance(error, dict):
        return None

    explicit = (
        error.get("category")
        or error.get("error_category")
        or error.get("type")
    )
    if explicit:
        normalized = CalibrationEngine.normalize_label(explicit)
        return None if normalized == "none" else normalized

    check_name = error.get("check") or error.get("check_name")
    if check_name:
        mapped = CHECK_CATEGORY_MAP.get(str(check_name).lower())
        if mapped:
            return mapped

    return None


def extract_predicted_categories(result):
    """Extract unique predicted categories from an evaluator result."""

    predicted = set()

    for error in result.get("errors", []):
        category = _category_from_error(error)
        if category:
            predicted.add(category)

    checks_container = result.get("checks", {})
    if isinstance(checks_container, dict):
        deterministic = checks_container.get("deterministic", {})
        if isinstance(deterministic, dict):
            for check in deterministic.get("errors", []):
                category = _category_from_error(check)
                if category:
                    predicted.add(category)

            for check in deterministic.get("checks", []):
                if not isinstance(check, dict):
                    continue
                if check.get("status") in ("failed", "warning"):
                    category = _category_from_error(check)
                    if category:
                        predicted.add(category)

        # LLM judge results may be stored under different keys by versions.
        for key in ("llm_judge", "llm", "semantic_judge"):
            judge = checks_container.get(key)
            if isinstance(judge, dict):
                for error in judge.get("errors", []):
                    category = _category_from_error(error)
                    if category:
                        predicted.add(category)

    return sorted(predicted) if predicted else ["none"]


def run_human_calibration(calibration_path, results, output_path):
    """
    Match human-labelled examples to evaluated item IDs and calculate
    binary detection and per-category multi-label metrics.
    """

    human_records = load_calibration_jsonl(calibration_path)
    result_by_id = {str(item.get("id")): item for item in results}

    matched_ids = [
        str(record["id"])
        for record in human_records
        if str(record["id"]) in result_by_id
    ]
    unmatched_ids = [
        str(record["id"])
        for record in human_records
        if str(record["id"]) not in result_by_id
    ]

    if not matched_ids:
        raise ValueError(
            "No calibration IDs matched evaluated dataset IDs. "
            "The calibration JSONL must use the same item IDs as the dataset."
        )

    matched_records = [
        record for record in human_records
        if str(record["id"]) in result_by_id
    ]

    actual_sets = [
        set(record["_normalized_labels"])
        for record in matched_records
    ]
    predicted_sets = [
        set(extract_predicted_categories(
            result_by_id[str(record["id"])]
        ))
        for record in matched_records
    ]

    actual_multi = [
        sorted(labels) if labels else ["none"]
        for labels in actual_sets
    ]
    predicted_multi = [
        sorted(labels) if labels else ["none"]
        for labels in predicted_sets
    ]

    engine = CalibrationEngine()
    detection = engine.evaluate_detection(actual_multi, predicted_multi)
    multilabel = engine.evaluate_multilabel(actual_multi, predicted_multi)

    categories = sorted(
        (set().union(*actual_sets, *predicted_sets)) - {"none"}
    )
    per_category = {}

    for category in categories:
        tp = sum(
            category in actual and category in predicted
            for actual, predicted in zip(actual_sets, predicted_sets)
        )
        fp = sum(
            category not in actual and category in predicted
            for actual, predicted in zip(actual_sets, predicted_sets)
        )
        fn = sum(
            category in actual and category not in predicted
            for actual, predicted in zip(actual_sets, predicted_sets)
        )

        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall else 0.0
        )

        per_category[category] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "support": sum(category in labels for labels in actual_sets),
        }

    # Exact-set match is a useful additional multi-label measure.
    exact_matches = sum(
        actual == predicted
        for actual, predicted in zip(actual_sets, predicted_sets)
    )

    report = {
        "status": "completed",
        "source": str(calibration_path),
        "total_human_label_records": len(human_records),
        "matched_records": len(matched_ids),
        "unmatched_ids": unmatched_ids,
        "exact_match_accuracy": round(
            exact_matches / len(matched_ids), 4
        ),
        "detection_metrics": detection,
        "multilabel_metrics": multilabel,
        "per_category": per_category,
        "notes": [
            "Metrics are computed only for human-labelled IDs also present "
            "in the evaluated dataset.",
            "Category metrics use multi-label one-vs-rest counting.",
            "Unmatched calibration IDs are excluded and listed explicitly.",
            "These metrics reflect the supplied human-labelled sample only."
        ],
    }

    save_json(output_path, report)
    return report



# =========================================================
# MAIN CLI
# =========================================================

def main():

    parser = argparse.ArgumentParser(
        description="IndicEval: Translation and Speech Quality Harness"
    )

    parser.add_argument(
        "--dataset",
        required=True,
        help="Path to input JSONL dataset",
    )

    parser.add_argument(
        "--langs",
        required=True,
        help="Comma-separated language codes",
    )

    parser.add_argument(
        "--out",
        required=True,
        help="Output directory",
    )

    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate missing translations and speech audio",
    )

    parser.add_argument(
        "--baseline",
        default=None,
        help="Previous summary.json for comparison",
    )

    parser.add_argument(
        "--calibration",
        default=None,
        help="Human-labelled calibration JSONL file",
    )

    parser.add_argument(
        "--max-cost-inr",
        type=float,
        default=None,
        help="Maximum Bodhan API budget in INR (does not cap external LLM judge usage)",
    )

    args = parser.parse_args()

    setup_logging()

    dataset_path = Path(args.dataset)
    output_dir = Path(args.out)

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not dataset_path.exists():
        raise FileNotFoundError(
            f"Dataset not found: {dataset_path}"
        )

    selected_languages = [
        language.strip().lower()
        for language in args.langs.split(",")
        if language.strip()
    ]

    if not selected_languages:
        raise ValueError(
            "Please provide at least one language using --langs."
        )

    logging.info("Starting IndicEval")
    logging.info("Dataset: %s", dataset_path)
    logging.info("Languages: %s", selected_languages)

    if args.max_cost_inr is not None:

        if args.max_cost_inr < 0:
            raise ValueError(
                "--max-cost-inr cannot be negative."
            )

        logging.info(
            "Bodhan API budget enforcement enabled: ₹%.2f "
            "(external LLM judge usage is not capped by this option)",
            args.max_cost_inr,
        )

    # Load dataset
    records = load_jsonl(dataset_path)

    # Filter languages
    records = [
        record
        for record in records
        if str(record["lang"]).lower() in selected_languages
    ]

    if not records:
        raise ValueError(
            "No records found for the selected languages."
        )

    logging.info(
        "Loaded %d dataset records.",
        len(records),
    )

    client = BodhanClient(max_cost_inr=args.max_cost_inr)

    # Optional generation
    if args.generate:
        logging.info(
            "Generating missing translations and audio..."
        )

        records = generate_missing_outputs(
            records,
            client,
            output_dir,
        )

    # -----------------------------------------------------
    # SPEECH EVALUATION - PER ITEM
    # -----------------------------------------------------

    records_with_audio = sum(
        bool(record.get("audio"))
        for record in records
    )

    records_without_audio = len(records) - records_with_audio

    logging.info(
        "Records with audio: %d",
        records_with_audio,
    )

    logging.info(
        "Records without audio: %d",
        records_without_audio,
    )

    # IMPORTANT:
    # Keep speech evaluation enabled.
    # evaluator.py handles missing audio independently.
    run_speech = True

    # Initialize evaluator
    evaluator = IndicEvalEvaluator(
        run_back_translation=True,
        run_llm_judge=True,
        run_speech=run_speech,
        bodhan_client=client,
    )

    logging.info("Running quality evaluation...")

    filtered_dataset_path = (
        output_dir / "filtered_dataset.jsonl"
    )

    save_jsonl(
        filtered_dataset_path,
        records,
    )

    try:
        results = evaluator.evaluate_dataset(
            str(filtered_dataset_path)
        )
    finally:
        filtered_dataset_path.unlink(
            missing_ok=True
        )

    # Save individual results
    save_jsonl(
        output_dir / "items.jsonl",
        results,
    )

    # Collect Bodhan and external LLM usage after all evaluations.
    usage_report = client.get_usage_report()
    llm_usage_report = (
        evaluator.llm_judge.get_usage_report()
        if evaluator.llm_judge is not None
        and hasattr(evaluator.llm_judge, "get_usage_report")
        else {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_inr": 0.0,
            "unpriced": True,
            "cost_is_partial": True,
            "reason": "LLM judge usage report is unavailable."
        }
    )

    summary = create_summary(
        results,
        output_dir,
        selected_languages,
        usage_report=usage_report,
        llm_usage_report=llm_usage_report,
    )

    # Generate baseline comparison
    create_diff(
        summary,
        args.baseline,
        output_dir,
    )

    # Human-label calibration
    calibration_output_path = output_dir / "calibration.json"

    if args.calibration:
        logging.info(
            "Running human-label calibration using %s",
            args.calibration,
        )
        calibration_report = run_human_calibration(
            calibration_path=args.calibration,
            results=results,
            output_path=calibration_output_path,
        )
        logging.info(
            "Calibration completed: %d matched human-labelled items.",
            calibration_report["matched_records"],
        )
        logging.info(
            "Detection precision: %.4f | recall: %.4f | F1: %.4f",
            calibration_report["detection_metrics"]["precision"],
            calibration_report["detection_metrics"]["recall"],
            calibration_report["detection_metrics"]["f1_score"],
        )
    else:
        save_json(
            calibration_output_path,
            {
                "status": "not_run",
                "reason": (
                    "No --calibration JSONL file was supplied. "
                    "Provide human-labelled item IDs to calculate metrics."
                ),
            },
        )

    # Final output
    logging.info("------------------------------------")
    logging.info("IndicEval evaluation completed.")
    logging.info("Total items: %d", summary["total_items"])
    logging.info("Passed: %d", summary["passed"])
    logging.info("Failed: %d", summary["failed"])
    logging.info("Warnings: %d", summary["warning"])
    logging.info(
        "Pass rate: %.2f%%",
        summary["pass_rate"] * 100,
    )
    logging.info("------------------------------------")

    logging.info(
        "Reports saved to: %s",
        output_dir.resolve(),
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    try:
        main()

    except Exception as exc:
        logging.exception(
            "IndicEval failed: %s",
            exc,
        )
        sys.exit(1) 