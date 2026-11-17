"""
Unit tests for text normalization and CER metric (src/bodhan_core/normalize.py).
"""
from bodhan_core.normalize import normalize_text, digit_normalize, compute_cer


def test_normalize_text():
    raw = "   \n\tनमस्ते   भारत \n\n"
    norm = normalize_text(raw)
    assert norm == "नमस्ते भारत"


def test_digit_normalize():
    # Devanagari numerals १२३४५ -> 12345
    dev_num = "संख्या १२३४५ है"
    ascii_num = digit_normalize(dev_num)
    assert "12345" in ascii_num

    # Bengali numerals ১২৩৪৫ -> 12345
    beng_num = "নম্বর ১২৩৪৫"
    assert "12345" in digit_normalize(beng_num)


def test_compute_cer_identical():
    ref = "This is a clean sentence."
    hyp = "This is a clean sentence."
    assert compute_cer(ref, hyp) == 0.0


def test_compute_cer_difference():
    ref = "नमस्ते भारत"
    hyp = "नमस्ते"
    cer = compute_cer(ref, hyp)
    assert cer > 0.0
    assert cer < 1.0


def test_compute_cer_empty():
    assert compute_cer("", "") == 0.0
    assert compute_cer("", "something") == 1.0
