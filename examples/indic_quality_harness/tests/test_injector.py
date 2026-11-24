
from bodhan_eval.injector import inject_error


def test_number_injection():
    result = inject_error("कीमत 500 रुपये है", "number_mismatch")

    assert result is not None
    assert result["mutated_text"] == "कीमत 999 रुपये है"
    assert result["injected_category"] == "number_mismatch"


def test_omission_injection():
    result = inject_error("बैठक सुबह शुरू होगी", "omission", seed=1)

    assert result is not None
    assert result["mutated_text"] != "बैठक सुबह शुरू होगी"
    assert result["injected_category"] == "omission"


def test_repetition_injection():
    result = inject_error("बैठक सुबह शुरू होगी", "repetition", seed=1)

    assert result is not None
    assert result["mutated_text"] != "बैठक सुबह शुरू होगी"
    assert result["injected_category"] == "repetition"


def test_formatting_injection():
    result = inject_error("Use **bold** text", "formatting_mismatch")

    assert result is not None
    assert result["mutated_text"] == "Use bold text"
    assert result["injected_category"] == "formatting_mismatch"


def test_injection_is_deterministic():
    first = inject_error("बैठक सुबह शुरू होगी", "omission", seed=42)
    second = inject_error("बैठक सुबह शुरू होगी", "omission", seed=42)

    assert first == second


def test_number_injection_without_numbers_returns_none():
    result = inject_error("कोई संख्या नहीं है", "number_mismatch")

    assert result is None
