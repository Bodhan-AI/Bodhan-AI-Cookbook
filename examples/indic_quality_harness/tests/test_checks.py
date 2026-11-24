from bodhan_eval.checks import (
    check_formatting,
    check_numbers,
    run_deterministic_checks,
)
from bodhan_eval.normalize import normalize_text


def test_bengali_digits_normalize_to_english():
    assert normalize_text("১০০") == "100"


def test_hindi_digits_normalize_to_english():
    assert normalize_text("१२३") == "123"


def test_matching_numbers_pass():
    result = check_numbers("Price is 500 rupees", "कीमत 500 रुपये है")
    assert result["passed"] is True


def test_different_numbers_fail():
    result = check_numbers("Price is 500 rupees", "कीमत 300 रुपये है")
    assert result["passed"] is False
    assert result["severity"] == "high"


def test_matching_formatting_passes():
    result = check_formatting("This is **important**", "यह **महत्वपूर्ण** है")
    assert result["passed"] is True


def test_missing_formatting_fails():
    result = check_formatting("This is **important**", "यह महत्वपूर्ण है")
    assert result["passed"] is False


def test_record_without_translation_has_no_checks():
    record = {
        "id": "hi-003",
        "language": "hi",
        "source_text": "Hello",
    }
    assert run_deterministic_checks(record) == []
