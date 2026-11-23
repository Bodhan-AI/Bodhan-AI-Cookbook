"""
Unit tests for protected spans, placeholders, and number preservation.
"""

from bodhan_dub.text.spans import (
    extract_numbers,
    protect_terms,
    restore_terms,
    verify_numbers_preserved,
)


def test_protect_terms_priority_and_boundaries():
    text = "We study Quantum Computing and Computing principles."
    terms = ["Computing", "Quantum Computing"]

    # "Quantum Computing" must be protected before "Computing"
    protected, ph_map = protect_terms(text, terms)
    assert "__TERM_0__" in protected
    assert "__TERM_1__" in protected
    assert ph_map["__TERM_0__"] == "Quantum Computing"
    assert ph_map["__TERM_1__"] == "Computing"


def test_restore_terms_survival():
    ph_map = {"__TERM_0__": "Quantum Computing"}
    canonical = {"quantum computing": "क्वांटम कंप्यूटिंग"}

    translated_good = "यह __TERM_0__ का एक उदाहरण है।"
    restored, survived = restore_terms(translated_good, ph_map, canonical)
    assert survived is True
    assert "क्वांटम कंप्यूटिंग" in restored

    # Corrupted / dropped placeholder
    translated_bad = "यह तकनीक का एक उदाहरण है।"
    _, survived_bad = restore_terms(translated_bad, ph_map, canonical)
    assert survived_bad is False


def test_extract_numbers_indic_and_ascii():
    text = "Room 102 has 45 students and 3.5 hours."
    nums = extract_numbers(text)
    assert nums == ["102", "45", "3.5"]

    # Devanagari numerals: १०२ (102) and ४५ (45)
    text_indic = "कमरा १०२ में ४५ छात्र हैं।"
    nums_indic = extract_numbers(text_indic)
    assert nums_indic == ["102", "45"]


def test_verify_numbers_preserved():
    src = "The meeting is at 9 in room 402."
    tgt_good = "बैठक कमरा ४०२ में ९ बजे है।"
    assert verify_numbers_preserved(src, tgt_good) is True

    tgt_missing = "बैठक कमरा ४०२ में है।"  # dropped 9
    assert verify_numbers_preserved(src, tgt_missing) is False
