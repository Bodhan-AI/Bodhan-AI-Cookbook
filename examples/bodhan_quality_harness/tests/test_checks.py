"""Unit tests for normalization, spans and the deterministic checks.

These run with no network and no API keys — the point is that the trustworthy
part of the harness is provable offline.
"""

from __future__ import annotations

import pytest

from bodhan_eval.checks import Flag, ItemContext
from bodhan_eval.checks import format as fmt
from bodhan_eval.checks import length as length_checks
from bodhan_eval.checks import numbers as num
from bodhan_eval.checks import script as script_checks
from bodhan_eval.checks import terms as term_checks
from bodhan_eval.checks.base import dedupe_flags
from bodhan_eval.normalize import (
    expected_script, normalize, script_conformance, to_ascii_digits,
)
from bodhan_eval.spans import overlap, overlaps, span_preserving


# --- normalization -----------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("२५", "25"),          # devanagari
    ("২৫", "25"),          # bengali
    ("௨௫", "25"),          # tamil
    ("१०० °C", "100 °C"),
])
def test_indic_digits_become_ascii(text, expected):
    assert to_ascii_digits(text) == expected


def test_normalize_collapses_punctuation_and_space():
    assert normalize("  Hello\u00a0 world  ") == "Hello world"
    assert normalize("क।") == "क."


def test_expected_script_mapping():
    assert expected_script("hi") == "devanagari"
    assert expected_script("ta") == "tamil"
    assert expected_script("en") is None


def test_script_conformance_detects_wrong_script():
    frac, detected = script_conformance("Hello world", "hi")
    assert frac == 0.0
    assert detected is None


def test_script_conformance_accepts_right_script():
    frac, _ = script_conformance("पानी सौ डिग्री पर उबलता है", "hi")
    assert frac > 0.9


# --- spans -------------------------------------------------------------------
def test_span_preserving_keeps_length():
    text = "तापमान २५ °C है"
    assert len(span_preserving(text)) == len(text)


def test_overlap_maths():
    assert overlaps((0, 5), (4, 9))
    assert not overlaps((0, 5), (5, 9))
    assert overlap((0, 10), (0, 10)) == 1.0
    assert overlap(None, (0, 1)) == 0.0


# --- numbers -----------------------------------------------------------------
def _ctx(source, translation, lang="hi", **kw):
    return ItemContext(id="t", source=source, lang=lang, translation=translation, **kw)


def test_changed_digit_is_caught_and_localized():
    ctx = _ctx("Water boils at 100 °C.", "पानी 900 °C पर उबलता है।")
    flags = num.check_numbers(ctx)
    assert flags, "a changed digit must be flagged"
    f = flags[0]
    assert f.category == "number"
    assert f.span is not None
    assert "900" in ctx.translation[f.span[0]:f.span[1]]


def test_preserved_number_is_not_flagged():
    ctx = _ctx("Water boils at 100 °C.", "पानी 100 °C पर उबलता है।")
    assert num.check_numbers(ctx) == []


def test_indic_digits_match_ascii_numbers():
    ctx = _ctx("There are 25 students.", "यहाँ २५ छात्र हैं।")
    assert num.check_numbers(ctx) == []


def test_single_digit_spelled_out_is_not_flagged():
    # "3" -> "three" is a legitimate translation choice, not an error.
    ctx = _ctx("There are 3 apples.", "यहाँ तीन सेब हैं।")
    assert num.check_numbers(ctx) == []


# --- script ------------------------------------------------------------------
def test_wrong_script_flagged():
    ctx = _ctx("Water boils.", "Water boils.", lang="hi")
    cats = {f.category for f in script_checks.check_script(ctx)}
    assert "language" in cats


def test_untranslated_detected():
    ctx = _ctx("Water boils at 100 C.", "Water boils at 100 C.", lang="hi")
    checks = {f.check for f in script_checks.check_untranslated(ctx)}
    assert "script.untranslated" in checks


def test_markdown_tables_do_not_trip_latin_ratio():
    src = "| Element | Symbol |\n| --- | --- |\n| Oxygen | O |"
    tgt = "| तत्व | प्रतीक |\n| --- | --- |\n| ऑक्सीजन | O |"
    ctx = _ctx(src, tgt, lang="hi")
    assert script_checks.check_latin_ratio(ctx) == []


# --- format ------------------------------------------------------------------
def test_lost_table_row_flagged():
    src = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    tgt = "| अ | ब |\n| 1 | 2 |"
    flags = fmt.check_format(_ctx(src, tgt))
    assert any(f.category == "format" for f in flags)


def test_latex_command_loss_flagged():
    src = r"The area is $A = \pi r^2$."
    tgt = "क्षेत्रफल pi r वर्ग है।"
    flags = fmt.check_format(_ctx(src, tgt))
    assert any(f.category == "format" for f in flags)


def test_intact_structure_not_flagged():
    src = "## Title\n\n| A | B |\n| --- | --- |\n| 1 | 2 |"
    tgt = "## शीर्षक\n\n| अ | ब |\n| --- | --- |\n| 1 | 2 |"
    assert fmt.check_format(_ctx(src, tgt)) == []


# --- length and repetition ---------------------------------------------------
def test_short_translation_flags_omission():
    src = " ".join(["word"] * 40)
    tgt = " ".join(["शब्द"] * 8)
    flags = length_checks.check_length(_ctx(src, tgt))
    assert any(f.category == "omission" for f in flags)


def test_repeated_phrase_flags_addition():
    tgt = "यह एक वाक्य है जो दोहराया गया है यह एक वाक्य है जो दोहराया गया है"
    flags = length_checks.check_repeats(_ctx("A sentence.", tgt))
    assert any(f.category == "addition" for f in flags)


def test_no_repeat_no_flag():
    tgt = "यह पहला वाक्य है और यह दूसरा वाक्य है जो अलग है"
    assert length_checks.check_repeats(_ctx("Two sentences.", tgt)) == []


# --- terms -------------------------------------------------------------------
def test_acronym_missing_flagged():
    flags = term_checks.check_terms(_ctx("DNA carries information.", "यह जानकारी ले जाता है।"))
    assert any("DNA" in f.evidence for f in flags)


def test_transliterated_proper_noun_not_flagged():
    # Chandrayaan is transliterated, not preserved — must not be a false alarm.
    flags = term_checks.check_terms(
        _ctx("ISRO launched Chandrayaan-3.", "इसरो ने चंद्रयान-3 लॉन्च किया।"))
    assert not any("Chandrayaan" in f.evidence for f in flags)


# --- dedupe ------------------------------------------------------------------
def test_dedupe_merges_overlapping_same_category():
    a = Flag("language", "minor", (0, 10), "first", "a")
    b = Flag("language", "critical", (2, 12), "second", "b")
    merged = dedupe_flags([a, b])
    assert len(merged) == 1
    assert merged[0].severity == "critical"


def test_dedupe_keeps_distinct_categories():
    a = Flag("language", "minor", (0, 10), "x", "a")
    b = Flag("number", "major", (0, 10), "y", "b")
    assert len(dedupe_flags([a, b])) == 2


# --- injector labels must match what the checks emit -------------------------
def test_repetition_is_labelled_addition_not_mistranslation():
    """Regression: a duplicated phrase is content not in the source.

    Labelling it "mistranslation" made the category metrics measure the wrong
    thing — every repetition in the demo set was scored as a mistranslation.
    """
    from bodhan_eval.inject import inject_repetition
    import random

    text = "यह एक लंबा वाक्य है जिसमें कई शब्द हैं और यह दोहराया जा सकता है"
    res = inject_repetition(text, random.Random(1))
    assert res is not None
    assert res.label["type"] == "addition"
    assert res.label["note"] == "duplicated phrase (repetition)"


def test_every_injected_label_uses_a_real_category():
    from bodhan_eval.inject import inject_item
    from bodhan_eval.checks.base import CATEGORIES

    text = "| A | B |\n| --- | --- |\n| 1 | 2 | यह 25 इकाइयों वाला वाक्य है। और दूसरा वाक्य।"
    for v in inject_item("x", "Source with 25 units and HTTP.", text, "hi", seed=3):
        for lab in v["labels"]:
            assert lab["type"] in CATEGORIES, f"unknown category {lab['type']}"
