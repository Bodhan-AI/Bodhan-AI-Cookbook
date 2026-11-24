
from bodhan_eval.calibration import evaluate_injected_cases


def test_number_error_is_detected():
    result = evaluate_injected_cases([
        {
            "id": "number-1",
            "clean_translation": "कीमत 500 रुपये है",
            "mutated_text": "कीमत 999 रुपये है",
            "injected_category": "number_mismatch",
        }
    ])

    assert result["metrics"]["overall"]["recall"] == 1.0


def test_omission_error_is_detected():
    result = evaluate_injected_cases([
        {
            "id": "omission-1",
            "clean_translation": "बैठक सुबह शुरू होगी",
            "mutated_text": "बैठक सुबह होगी",
            "injected_category": "omission",
        }
    ])

    assert result["metrics"]["categories"]["omission"]["recall"] == 1.0


def test_repetition_error_is_detected():
    result = evaluate_injected_cases([
        {
            "id": "repetition-1",
            "clean_translation": "बैठक सुबह शुरू होगी",
            "mutated_text": "बैठक सुबह शुरू शुरू होगी",
            "injected_category": "repetition",
        }
    ])

    assert result["metrics"]["categories"]["repetition"]["recall"] == 1.0


def test_clean_translation_has_no_false_alarm():
    result = evaluate_injected_cases([
        {
            "id": "clean-1",
            "clean_translation": "Use **bold** text",
            "mutated_text": "Use **bold** text",
            "injected_category": "formatting_mismatch",
        }
    ])

    assert result["metrics"]["overall"]["fp"] == 0
