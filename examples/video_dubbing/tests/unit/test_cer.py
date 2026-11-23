"""
Unit tests for CER calculation, text normalization, and tail omission detection.
"""

from bodhan_dub.text.cer import compute_cer, detect_tail_omission


def test_cer_identical_strings():
    ref = "यह एक परीक्षण वाक्य है।"
    hyp = "यह एक परीक्षण वाक्य है।"
    assert compute_cer(ref, hyp) == 0.0


def test_cer_empty_strings():
    assert compute_cer("", "") == 0.0
    assert compute_cer("नमस्ते", "") == 1.0
    assert compute_cer("", "नमस्ते") == 1.0


def test_cer_minor_edit():
    ref = "भारत एक महान देश है।"
    hyp = "भारत एक महान देश"  # omitted punctuation and auxiliary verb
    cer = compute_cer(ref, hyp)
    assert 0.0 < cer < 0.35


def test_tail_omission_detection():
    ref = "This is a complete sentence that finishes here."
    hyp = "This is a complete sentence that"
    omitted, tail = detect_tail_omission(ref, hyp)
    assert omitted is True
    assert "here" in tail


def test_tail_omission_not_triggered_when_complete():
    ref = "This is a complete sentence."
    hyp = "This is a complete sentence."
    omitted, tail = detect_tail_omission(ref, hyp)
    assert omitted is False
    assert tail == ""
