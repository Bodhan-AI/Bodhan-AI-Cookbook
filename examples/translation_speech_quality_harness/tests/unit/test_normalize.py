"""Normalisation: digits, Unicode variants, punctuation, offsets back to the original (TESTING.md §1)."""

from __future__ import annotations

import pytest

from bodhan_eval.normalize import (
    ascii_digits,
    canonical_number,
    content_words,
    normalize_text,
    normalize_with_map,
    segments,
    stem,
    word_tokens,
)

# Code point of the digit zero in every decimal-digit system the harness may meet.
DIGIT_ZEROS = {
    "devanagari": 0x0966,
    "bengali": 0x09E6,
    "gurmukhi": 0x0A66,
    "gujarati": 0x0AE6,
    "odia": 0x0B66,
    "tamil": 0x0BE6,
    "telugu": 0x0C66,
    "kannada": 0x0CE6,
    "malayalam": 0x0D66,
    "arabic_indic": 0x0660,
    "extended_arabic_indic": 0x06F0,
    "ol_chiki": 0x1C50,
    "meetei_mayek": 0xABF0,
}


def digits_in(zero: int, number: str) -> str:
    return "".join(chr(zero + int(d)) for d in number)


@pytest.mark.parametrize("zero", DIGIT_ZEROS.values(), ids=DIGIT_ZEROS.keys())
def test_every_indic_digit_system_maps_to_ascii(zero: int) -> None:
    native = digits_in(zero, "2509")
    assert normalize_text(native) == "2509"
    assert ascii_digits(native) == "2509"
    assert canonical_number(native) == "2509"


def test_ascii_digits_is_length_preserving() -> None:
    text = "पानी १०० °C पर उबलता है, ২৫ और ௩"
    out = ascii_digits(text)
    assert len(out) == len(text)
    assert "100" in out and "25" in out and "3" in out


def test_nukta_precomposed_and_decomposed_compare_equal() -> None:
    precomposed = "क़"  # क़ as one code point
    decomposed = "क़"  # क + nukta
    assert normalize_text(precomposed) == normalize_text(decomposed)


def test_zero_width_joiners_are_removed() -> None:
    assert normalize_text("क्‍ष") == normalize_text("क्ष")
    assert normalize_text("a‌b﻿") == "ab"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("“quoted”", '"quoted"', id="curly-double-quotes"),
        pytest.param("it’s", "it's", id="curly-apostrophe"),
        pytest.param("10–20", "10-20", id="en-dash"),
        pytest.param("a—b", "a-b", id="em-dash"),
        pytest.param("वाक्य।", "वाक्य.", id="danda"),
        pytest.param("श्लोक॥", "श्लोक.", id="double-danda"),
        pytest.param("a  b", "a b", id="exotic-spaces-collapse"),
        pytest.param("  A   B \n C ", "a b c", id="whitespace-collapse-casefold"),
    ],
)
def test_punctuation_and_space_unified(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_asr_fold_drops_nukta_and_candrabindu() -> None:
    assert normalize_text("ज़रा", asr_fold=True) == normalize_text("जरा", asr_fold=True)
    assert normalize_text("हँस", asr_fold=True) == normalize_text("हंस", asr_fold=True)
    assert normalize_text("ज़रा") != normalize_text("जरा")  # only folded when asked


@pytest.mark.parametrize(
    ("original", "needle", "expected"),
    [
        pytest.param("पानी  १०० °C पर।", "100", "१००", id="native-digits"),
        pytest.param("The “big”   Dog", '"big" dog', "“big”   Dog", id="quotes-spaces-case"),
        pytest.param("क़लम‍ से", "से", "से", id="after-nukta-and-zwj"),
    ],
)
def test_to_original_round_trips_spans(original: str, needle: str, expected: str) -> None:
    norm = normalize_with_map(original)
    a = norm.text.index(needle)
    s, e = norm.to_original(a, a + len(needle))
    assert original[s:e] == expected


def test_to_original_on_empty_text() -> None:
    assert normalize_with_map("").to_original(0, 3) == (0, 0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,00,000", "100000"),
        ("100,000", "100000"),
        ("3.50", "3.5"),
        ("3.0", "3"),
        ("007", "7"),
        ("0.5", "0.5"),
        ("२५", "25"),
        ("1.2.3", "1.2.3"),
    ],
)
def test_canonical_number(raw: str, expected: str) -> None:
    assert canonical_number(raw) == expected


@pytest.mark.parametrize(
    "group",
    [
        pytest.param(["boil", "boils", "boiling"], id="boil"),
        pytest.param(["time", "times"], id="time"),
        pytest.param(["stop", "stopped"], id="stop"),
        pytest.param(["study", "studies", "studied"], id="study"),
    ],
)
def test_stem_equates_inflections(group: list[str]) -> None:
    assert len({stem(w) for w in group}) == 1


def test_stem_keeps_double_s() -> None:
    assert stem("glass") == "glass"


def test_content_words_drop_stopwords_and_canonicalise_numbers() -> None:
    words = [w for w, _, _ in content_words("Water boils at 1,000 degrees and three times")]
    assert words == ["water", "boil", "1000", "degre", "3", "tim"]


def test_segments_keep_decimals_and_split_on_danda() -> None:
    text = "Pi is 3.14 roughly. Next one!\nपहला वाक्य। दूसरा वाक्य।"
    segs = [text[a:b] for a, b in segments(text)]
    assert segs == ["Pi is 3.14 roughly.", "Next one!", "पहला वाक्य।", "दूसरा वाक्य।"]


def test_segments_skip_blank_lines_and_trim() -> None:
    text = "  One.  \n\n   \nTwo"
    assert [text[a:b] for a, b in segments(text)] == ["One.", "Two"]


def test_word_tokens_keep_indic_marks_and_numbers_together() -> None:
    text = "पानी 3.14 और 1,00,000 किलोमीटर"
    assert [t.text for t in word_tokens(text)] == ["पानी", "3.14", "और", "1,00,000", "किलोमीटर"]
    for t in word_tokens(text):
        assert text[t.start : t.end] == t.text
