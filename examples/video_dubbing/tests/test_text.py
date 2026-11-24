from __future__ import annotations

import pytest

from bodhan_dub import text


def test_fold_devanagari_digits() -> None:
    assert text.fold_digits("२५ मीटर") == "25 मीटर"


def test_fold_mixed_scripts() -> None:
    assert text.fold_digits("௧௨ and ౩౪ and 12") == "12 and 34 and 12"


def test_normalize_for_compare_ignores_script_of_digits() -> None:
    assert text.normalize_for_compare("२५.") == text.normalize_for_compare("25")


def test_normalize_for_compare_drops_punctuation_and_case() -> None:
    assert text.normalize_for_compare("Hello, World!") == "helloworld"


def test_cer_is_zero_for_identical_text() -> None:
    assert text.char_error_rate("कोण 90 डिग्री है", "कोण 90 डिग्री है") == 0.0


def test_cer_is_zero_across_digit_scripts() -> None:
    assert text.char_error_rate("कीमत २५ रुपये", "कीमत 25 रुपये") == 0.0


def test_cer_detects_a_dropped_word() -> None:
    assert text.char_error_rate("the angle is ninety degrees", "the angle is degrees") > 0.2


def test_cer_with_empty_reference() -> None:
    assert text.char_error_rate("", "") == 0.0
    assert text.char_error_rate("", "anything") == 1.0


def test_levenshtein_simple_cases() -> None:
    assert text.levenshtein("kitten", "sitting") == 3
    assert text.levenshtein("", "abc") == 3
    assert text.levenshtein("abc", "abc") == 0


def test_collapse_repeats_catches_a_loop() -> None:
    looped = "the angle is ninety degrees " * 4
    cleaned, changed = text.collapse_repeats(looped)

    assert changed
    assert cleaned == "the angle is ninety degrees"


def test_collapse_repeats_leaves_ordinary_emphasis_alone() -> None:
    original = "this is very very important"
    cleaned, changed = text.collapse_repeats(original)

    assert not changed
    assert cleaned == original


def test_collapse_repeats_is_idempotent() -> None:
    once, _ = text.collapse_repeats("abc def ghi " * 5)
    twice, changed = text.collapse_repeats(once)

    assert not changed
    assert once == twice


@pytest.mark.parametrize("value", ["", "   ", "...", "।", "!?"])
def test_degenerate_values(value: str) -> None:
    assert text.is_degenerate(value)


@pytest.mark.parametrize("value", ["a", "कोण", "90"])
def test_non_degenerate_values(value: str) -> None:
    assert not text.is_degenerate(value)


@pytest.mark.parametrize("value", ["done.", "क्या?", "ठीक है।"])
def test_ends_sentence(value: str) -> None:
    assert text.ends_sentence(value)


def test_does_not_end_sentence_without_punctuation() -> None:
    assert not text.ends_sentence("and then")


def test_truncation_ratio_bounds() -> None:
    assert text.truncation_ratio("one two three", "one two three") == pytest.approx(1.0)
    assert text.truncation_ratio("one two three", "") == pytest.approx(0.0)
    assert 0.0 < text.truncation_ratio("one two three four", "one two") < 1.0
