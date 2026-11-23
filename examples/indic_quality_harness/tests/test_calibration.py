
from bodhan_eval.calibration import calculate_calibration


def test_perfect_detection():
    cases = [
        {
            "expected_categories": ["number_mismatch"],
            "predicted_categories": ["number_mismatch"],
        },
        {
            "expected_categories": ["omission"],
            "predicted_categories": ["omission"],
        },
        {
            "expected_categories": [],
            "predicted_categories": [],
        },
    ]

    result = calculate_calibration(cases)

    assert result["overall"]["precision"] == 1.0
    assert result["overall"]["recall"] == 1.0
    assert result["overall"]["f1"] == 1.0


def test_false_positive():
    cases = [
        {
            "expected_categories": [],
            "predicted_categories": ["number_mismatch"],
        },
    ]

    result = calculate_calibration(cases)

    assert result["overall"]["fp"] == 1
    assert result["overall"]["precision"] == 0.0


def test_false_negative():
    cases = [
        {
            "expected_categories": ["omission"],
            "predicted_categories": [],
        },
    ]

    result = calculate_calibration(cases)

    assert result["overall"]["fn"] == 1
    assert result["overall"]["recall"] == 0.0


def test_category_metrics_are_separate():
    cases = [
        {
            "expected_categories": ["number_mismatch"],
            "predicted_categories": ["number_mismatch"],
        },
        {
            "expected_categories": ["omission"],
            "predicted_categories": [],
        },
    ]

    result = calculate_calibration(cases)

    assert result["categories"]["number_mismatch"]["recall"] == 1.0
    assert result["categories"]["omission"]["recall"] == 0.0
