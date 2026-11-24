import json
from pathlib import Path
from collections import Counter

from bodhan_eval.evaluator import IndicEvalEvaluator
from bodhan_eval.calibration import CalibrationEngine


INPUT_FILE = Path("outputs/injected_dataset.jsonl")
DATASET_FILE = Path("sample_data/injection_benchmark.jsonl")
OUTPUT_DIR = Path("injection_benchmark_report")


# Map deterministic checks to error categories.
CHECK_CATEGORY_MAP = {
    "number_preservation": "number_error",
    "repetition": "repetition",
    "punctuation_integrity": "formatting_error",
    "markdown_integrity": "formatting_error",
    "latex_integrity": "formatting_error",
    "empty_translation": "omission",
    "length_anomaly": "omission",
    "omission_detection": "omission",
    "addition_detection": "addition"
}


# =========================================================
# FILE UTILITIES
# =========================================================

def load_jsonl(path):
    """Load JSONL records."""

    records = []

    with path.open("r", encoding="utf-8") as file:

        for line_number, line in enumerate(file, start=1):

            if not line.strip():
                continue

            try:
                records.append(json.loads(line))

            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON at line {line_number}: {exc}"
                )

    return records


def save_json(path, data):
    """Save JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:

        json.dump(
            data,
            file,
            indent=4,
            ensure_ascii=False
        )


def save_jsonl(path, records):
    """Save JSONL."""

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:

        for record in records:

            file.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )


# =========================================================
# GROUND TRUTH PROCESSING
# =========================================================

def extract_ground_truth_category(ground_truth):
    """
    Extract the expected category.

    Supports:
        "number_error"

    And:
        {"category": "number_error"}
    """

    if isinstance(ground_truth, str):

        return CalibrationEngine.normalize_label(
            ground_truth
        )

    if isinstance(ground_truth, dict):

        category = (
            ground_truth.get("category")
            or ground_truth.get("error_category")
            or ground_truth.get("type")
        )

        return CalibrationEngine.normalize_label(category)

    return "other"


# =========================================================
# DATASET PREPARATION
# =========================================================

def prepare_dataset():
    """Convert injected cases into evaluator-compatible JSONL."""

    if not INPUT_FILE.exists():

        raise FileNotFoundError(
            f"Injected dataset not found: {INPUT_FILE}\n"
            "Run the error injector first."
        )

    injected_cases = load_jsonl(INPUT_FILE)

    records = []

    for case in injected_cases:

        records.append({
            "id": case["id"],
            "source": case["original_translation"],
            "translation": case["corrupted_translation"],
            "lang": "en",
            "source_lang": "en",
            "target_lang": "en",
            "ground_truth": case["ground_truth"]
        })

    save_jsonl(DATASET_FILE, records)

    print(f"Prepared {len(records)} injected test cases.")
    print(f"Dataset saved to: {DATASET_FILE}")

    return records


# =========================================================
# PREDICTION EXTRACTION
# =========================================================

def extract_predicted_categories(result):
    """
    Extract predicted error categories.

    Actual evaluator structure:

        result
          |
          +-- checks
                |
                +-- deterministic
                      |
                      +-- checks
                            |
                            +-- individual check results

    Supports top-level errors for backward compatibility.
    """

    predicted = set()

    # Get nested deterministic results.
    checks_container = result.get("checks", {})

    deterministic = checks_container.get(
        "deterministic", {}
    ) if isinstance(checks_container, dict) else {}

    deterministic_checks = deterministic.get(
        "checks", []
    )

    # Inspect individual deterministic checks.
    for check in deterministic_checks:

        if not isinstance(check, dict):
            continue

        check_name = check.get("check")

        status = str(
            check.get("status", "")
        ).lower()

        # Failed or warning checks indicate detected issues.
        if status in ("failed", "warning"):

            category = CHECK_CATEGORY_MAP.get(check_name)

            if category:
                predicted.add(category)

    # Inspect top-level errors as an additional source.
    for error in result.get("errors", []):

        if not isinstance(error, dict):
            continue

        check_name = error.get("check")

        category = CHECK_CATEGORY_MAP.get(check_name)

        if category:
            predicted.add(category)

        explicit_category = error.get("category")

        if explicit_category:

            normalized = CalibrationEngine.normalize_label(
                explicit_category
            )

            if normalized != "none":
                predicted.add(normalized)

    if not predicted:
        return ["none"]

    return sorted(predicted)


# =========================================================
# EVALUATION COVERAGE
# =========================================================

def is_evaluation_complete(result):
    """Exclude incomplete evaluations from performance metrics."""

    evaluation_status = result.get(
        "evaluation_status",
        "completed"
    )

    evaluation_errors = result.get(
        "evaluation_errors", []
    )

    if evaluation_status in ("partial", "failed"):
        return False

    if evaluation_errors:
        return False

    return True


# =========================================================
# BENCHMARK RECORD CREATION
# =========================================================

def build_case_report(result, ground_truth):

    actual_category = extract_ground_truth_category(
        ground_truth
    )

    predicted_categories = extract_predicted_categories(
        result
    )

    return {
        "id": result.get("id"),

        "ground_truth": actual_category,

        "predicted_categories": predicted_categories,

        "detected_error": (
            predicted_categories != ["none"]
        ),

        "correct_category": (
            actual_category in predicted_categories
        ),

        "translation_status": result.get(
            "translation_status",
            result.get("status", "unknown")
        ),

        "evaluation_status": result.get(
            "evaluation_status",
            "completed"
        ),

        "evaluation_complete": is_evaluation_complete(
            result
        ),

        "evaluation_errors": result.get(
            "evaluation_errors", []
        )
    }


# =========================================================
# MAIN BENCHMARK
# =========================================================

def run_benchmark(records):

    print("\nInitializing deterministic evaluator...")

    evaluator = IndicEvalEvaluator(
        run_back_translation=False,
        run_llm_judge=False,
        run_speech=False
    )

    print("LLM judge: DISABLED")
    print("Back-translation: DISABLED")
    print("Speech evaluation: DISABLED")
    print("Bodhan API calls: NONE")

    results = evaluator.evaluate_dataset(
        str(DATASET_FILE)
    )

    # Keep original ground-truth labels.
    ground_truth_map = {
        record["id"]: record["ground_truth"]
        for record in records
    }

    benchmark_records = []

    for result in results:

        case_id = result.get("id")

        ground_truth = ground_truth_map.get(
            case_id,
            "other"
        )

        benchmark_records.append(
            build_case_report(
                result,
                ground_truth
            )
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # Save full evaluator output.
    save_jsonl(
        OUTPUT_DIR / "items.jsonl",
        results
    )

    # Save benchmark-specific predictions.
    save_jsonl(
        OUTPUT_DIR / "benchmark_items.jsonl",
        benchmark_records
    )

    # Exclude incomplete evaluations.
    valid_records = [
        record
        for record in benchmark_records
        if record["evaluation_complete"]
    ]

    excluded_records = [
        record
        for record in benchmark_records
        if not record["evaluation_complete"]
    ]

    if not valid_records:

        raise RuntimeError(
            "No complete evaluations available."
        )

    # =====================================================
    # METRICS
    # =====================================================

    engine = CalibrationEngine()

    actual = [
        record["ground_truth"]
        for record in valid_records
    ]

    predicted = [
        record["predicted_categories"]
        for record in valid_records
    ]

    # Single-label classification:
    # Use the first sorted category when multiple are predicted.
    single_predictions = [
        categories[0] if categories else "none"
        for categories in predicted
    ]

    classification_results = engine.evaluate(
        actual,
        single_predictions
    )

    # Binary detection metrics.
    detection_results = engine.evaluate_detection(
        actual,
        predicted
    )

    # Multi-label classification metrics.
    multilabel_results = engine.evaluate_multilabel(
        actual,
        predicted
    )

    # =====================================================
    # CASE-LEVEL ANALYSIS
    # =====================================================

    actual_error_records = [
        record
        for record in valid_records
        if record["ground_truth"] != "none"
    ]

    correctly_detected = sum(
        record["detected_error"]
        for record in actual_error_records
    )

    missed_errors = [
        record
        for record in actual_error_records
        if not record["detected_error"]
    ]

    false_positives = [
        record
        for record in valid_records
        if (
            record["ground_truth"] == "none"
            and record["detected_error"]
        )
    ]

    correct_categories = sum(
        record["correct_category"]
        for record in valid_records
    )

    category_counts = Counter(actual)

    # =====================================================
    # FINAL REPORT
    # =====================================================

    report = {

        "benchmark_name": (
            "IndicEval Error Injection Benchmark"
        ),

        "benchmark_type": "synthetic",

        "description": (
            "Evaluation against programmatically injected "
            "translation errors. These are synthetic labels, "
            "not human-annotated ground truth."
        ),

        "total_cases": len(benchmark_records),

        "completed_cases": len(valid_records),

        "excluded_cases": len(excluded_records),

        "ground_truth_distribution": dict(category_counts),

        "error_detection": detection_results,

        "category_classification": classification_results,

        "multi_label_classification": multilabel_results,

        "case_summary": {

            "actual_error_cases": len(actual_error_records),

            "correctly_detected_errors": correctly_detected,

            "missed_errors": len(missed_errors),

            "false_positives": len(false_positives),

            "correct_category_predictions": correct_categories
        },

        "excluded_case_ids": [
            record["id"]
            for record in excluded_records
        ],

        "missed_error_ids": [
            record["id"]
            for record in missed_errors
        ],

        "false_positive_ids": [
            record["id"]
            for record in false_positives
        ]
    }

    save_json(
        OUTPUT_DIR / "benchmark_report.json",
        report
    )

    save_json(
        OUTPUT_DIR / "calibration_metrics.json",
        {
            "benchmark_type": "synthetic",
            "detection": detection_results,
            "classification": classification_results,
            "multilabel": multilabel_results
        }
    )

    # =====================================================
    # TERMINAL SUMMARY
    # =====================================================

    print("\n" + "=" * 55)
    print("INDICEVAL - ERROR INJECTION BENCHMARK")
    print("=" * 55)

    print(f"Total cases: {len(benchmark_records)}")
    print(f"Completed evaluations: {len(valid_records)}")
    print(f"Excluded evaluations: {len(excluded_records)}")

    print("\nERROR DETECTION")
    print("-" * 55)

    print(
        f"Precision: {detection_results['precision']:.4f}"
    )

    print(
        f"Recall:    {detection_results['recall']:.4f}"
    )

    print(
        f"F1-score:  {detection_results['f1_score']:.4f}"
    )

    print("\nCATEGORY CLASSIFICATION")
    print("-" * 55)

    overall = classification_results["overall"]

    print(
        f"Accuracy:    {overall['accuracy']:.4f}"
    )

    print(
        f"Macro F1:    {overall['macro_f1']:.4f}"
    )

    print(
        f"Weighted F1: {overall['weighted_f1']:.4f}"
    )

    print("\nMULTI-LABEL CLASSIFICATION")
    print("-" * 55)

    print(
        f"Micro F1: {multilabel_results['micro_f1']:.4f}"
    )

    print(
        f"Macro F1: {multilabel_results['macro_f1']:.4f}"
    )

    print("\nINDIVIDUAL CASES")
    print("-" * 55)

    for record in benchmark_records:

        print(
            f"{record['id']} | "
            f"Actual: {record['ground_truth']} | "
            f"Predicted: {record['predicted_categories']} | "
            f"Complete: {record['evaluation_complete']}"
        )

    print("\nREPORTS GENERATED")
    print("-" * 55)

    print(OUTPUT_DIR / "items.jsonl")
    print(OUTPUT_DIR / "benchmark_items.jsonl")
    print(OUTPUT_DIR / "benchmark_report.json")
    print(OUTPUT_DIR / "calibration_metrics.json")

    print("\n" + "=" * 55)


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    prepared_records = prepare_dataset()

    run_benchmark(prepared_records)