import json

from bodhan_eval.calibration import CalibrationEngine


def main():

    engine = CalibrationEngine()

    print("=" * 60)
    print("INDICEVAL - CALIBRATION TEST")
    print("=" * 60)

    # Ground-truth labels assigned by humans.
    ground_truth = [
        "number_error",
        "omission",
        "repetition",
        "formatting_error",
        "addition",
        "none",
        "number_error",
        "omission",
        "none",
        "repetition"
    ]

    # Simulated predictions from the quality checker.
    predictions = [
        "number_error",
        "omission",
        "repetition",
        "formatting_error",
        "addition",
        "none",
        "number_error",
        "number_error",
        "none",
        "repetition"
    ]

    print("\n[TEST 1] Per-category calibration")

    results = engine.evaluate(
        ground_truth,
        predictions
    )

    print(json.dumps(results, indent=4))

    assert results["total_examples"] == 10
    assert "macro_f1" in results["overall"]
    assert "number_error" in results["per_category"]

    # Threshold tuning test.
    print("\n[TEST 2] Threshold tuning")

    confidence_scores = [
        0.95, 0.90, 0.85, 0.80,
        0.70, 0.60, 0.40, 0.30
    ]

    binary_labels = [
        1, 1, 1, 1,
        1, 0, 0, 0
    ]

    threshold_result = engine.tune_threshold(
        confidence_scores,
        binary_labels
    )

    print(json.dumps(threshold_result, indent=4))

    assert 0 <= threshold_result["threshold"] <= 1

    # Save calibration results.
    print("\n[TEST 3] Saving calibration results")

    output_path = engine.save(
        "outputs/calibration.json"
    )

    print("Saved:", output_path)

    print("\n" + "=" * 60)
    print("ALL CALIBRATION TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    main()