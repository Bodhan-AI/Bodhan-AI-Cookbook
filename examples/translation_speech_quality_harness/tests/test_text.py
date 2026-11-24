"""test_normalize: Normalization + offset map. Indic strings are either real Bodhan outputs
test_text_core: Script detection, protected regions, segmentation, spans, taxonomy, labels.
"""

import json
from pathlib import Path

import pytest

from bodhan_eval.config import CATEGORIES, LANGUAGES, PRODUCTION_CODES, canonical_category, canonical_severity
from bodhan_eval.dataset import _parse_label
from bodhan_eval.text import (
    char_script,
    clauses,
    expected_script,
    find_protected,
    foreign_runs,
    iou,
    mask_protected,
    normalize,
    normalize_with_map,
    overlaps,
    quote_to_span,
    script_profile,
    sentences,
    union_span,
)

# ==================== normalize
FX = json.loads((Path(__file__).parent / "fixtures" / "bodhan_outputs.json").read_text(encoding="utf-8"))


DIGIT_ZEROS = {
    "Devanagari": 0x0966, "Bengali": 0x09E6, "Gurmukhi": 0x0A66, "Gujarati": 0x0AE6, "Oriya": 0x0B66, "Tamil": 0x0BE6,
    "Telugu": 0x0C66, "Kannada": 0x0CE6, "Malayalam": 0x0D66, "Arabic-Indic": 0x0660, "Extended Arabic-Indic": 0x06F0,
    "Ol Chiki": 0x1C50, "Meetei Mayek": 0xABF0, "Fullwidth": 0xFF10,
}


@pytest.mark.parametrize("script,zero", DIGIT_ZEROS.items())
def test_digits_from_every_script_become_ascii(script, zero):
    twenty_five = chr(zero + 2) + chr(zero + 5)
    assert normalize(twenty_five) == "25", script
    assert normalize(f"{chr(zero + 1)},{chr(zero + 0)}{chr(zero + 0)}.{chr(zero + 5)}") == "1,00.5"


def test_nfc_unifies_nukta_and_two_part_vowels():
    assert normalize("क़") == normalize("क़")  # Devanagari KA + nukta == QA
    assert normalize("য়") == normalize("য়")  # Bengali YA + nukta == YYA
    assert normalize("கொ") == normalize("கொ")  # Tamil KA + E + AA == KA + O


def test_zero_width_removed_and_offsets_kept():
    s = "a‍b‌c"
    nt = normalize_with_map(s)
    assert nt.norm == "abc"
    assert nt.to_orig(1, 2) == (2, 4)  # 'b' chunk carries the trailing ZWNJ


def test_malayalam_old_chillu_equals_atomic():
    old = "അവന്‍"  # consonant + virama + ZWJ
    atomic = "അവൻ"
    assert normalize(old) == normalize(atomic)


def test_urdu_letter_variants_and_diacritics():
    assert normalize("كي") == normalize("کی")
    assert normalize("بَاـب") == normalize("باب")  # fatha + tatweel dropped


def test_punctuation_whitespace_and_case():
    assert normalize("  Hello  World।  ") == "hello world."
    assert normalize("“quoted” — x۔") == '"quoted" - x.'
    # Indic letters are never case-folded or altered
    hi = FX["translations"]["hi"]["translation"]
    assert normalize(hi).rstrip(".") == hi.rstrip("।")


def test_real_bengali_digits_map_back_to_original_offsets():
    bn = FX["translations"]["bn"]["translation"]  # real output, uses Bengali digits
    nt = normalize_with_map(bn)
    k = nt.norm.find("100")
    assert k >= 0
    s, e = nt.to_orig(k, k + 3)
    assert normalize(bn[s:e]) == "100" and bn[s:e] != "100"


def test_to_norm_inverse():
    hi = FX["translations"]["hi"]["translation"]
    nt = normalize_with_map(hi)
    k = hi.find("100")
    ns, ne = nt.to_norm(k, k + 3)
    assert nt.norm[ns:ne] == "100"
    assert nt.to_orig(ns, ne) == (k, k + 3)


def test_skeleton_drops_punct_and_space():
    assert normalize("a, b. c!", skeleton=True) == "abc"


class TestQuoteToSpan:
    hi = FX["translations"]["hi"]["translation"]

    def test_exact(self):
        q = self.hi[5:20]
        m = quote_to_span(self.hi, q)
        assert m.span == (5, 20) and m.method == "exact"

    def test_quote_without_final_danda_and_with_extra_spaces(self):
        words = self.hi.rstrip("।").split()
        q = "  ".join(words[-3:])  # double spaces, no danda
        m = quote_to_span(self.hi, q)
        assert m is not None and self.hi[m.span[0] : m.span[1]].split() == words[-3:]

    def test_digit_script_difference(self):
        bn = FX["translations"]["bn"]["translation"]
        m = quote_to_span(bn, "100")  # LLM quoted ASCII digits; text has Bengali digits
        assert m is not None and normalize(bn[m.span[0] : m.span[1]]) == "100"

    def test_not_found_returns_none(self):
        assert quote_to_span(self.hi, "this is not in the text") is None
        assert quote_to_span(self.hi, "   ") is None

    def test_ambiguous_quote_reports_occurrences(self):
        m = quote_to_span("ab ab ab", "ab")
        assert m.span == (0, 2) and m.occurrences == 3


# ==================== text_core
FX = json.loads((Path(__file__).parent / "fixtures" / "bodhan_outputs.json").read_text(encoding="utf-8"))


SCRIPT_LETTERS = {
    "Devanagari": range(0x0915, 0x091A), "Bengali": range(0x0995, 0x099A), "Gurmukhi": range(0x0A15, 0x0A1A),
    "Gujarati": range(0x0A95, 0x0A9A), "Oriya": range(0x0B15, 0x0B1A), "Tamil": [0x0B95, 0x0B99, 0x0B9A, 0x0B9C],
    "Telugu": range(0x0C15, 0x0C1A), "Kannada": range(0x0C95, 0x0C9A), "Malayalam": range(0x0D15, 0x0D1A),
    "Arabic": range(0x0628, 0x062D), "Latin": [ord(c) for c in "abcde"], "Meetei_Mayek": range(0xABC0, 0xABC5),
    "Ol_Chiki": range(0x1C5A, 0x1C5F),
}


def test_every_language_script_is_detectable():
    for code, lang in LANGUAGES.items():
        letters = "".join(chr(c) for c in SCRIPT_LETTERS[lang.script])
        prof = script_profile(letters + " 123 ।")
        assert prof.dominant == lang.script, code
        assert prof.ratio(expected_script(code)) == 1.0  # digits and danda are not letters


def test_all_13_production_languages_have_a_script():
    assert all(LANGUAGES[c].script for c in PRODUCTION_CODES)


@pytest.mark.parametrize("lang", ["hi", "ta", "bn"])
def test_real_bodhan_outputs_have_expected_script(lang):
    prof = script_profile(FX["translations"][lang]["translation"])
    assert prof.dominant == expected_script(lang)
    assert prof.ratio(expected_script(lang)) > 0.95  # Tamil keeps "C" of °C


def test_untranslated_output_is_latin():
    prof = script_profile(FX["source"])
    assert prof.dominant == "Latin" and prof.ratio("Devanagari") == 0.0


def test_foreign_latin_run_detected_and_protected_ignored():
    hi = FX["translations"]["hi"]["translation"]
    mixed = hi + " This clause was left untranslated by mistake."
    runs = foreign_runs(mixed, "Latin")
    assert len(runs) == 1 and mixed[runs[0].start : runs[0].end].startswith("This clause")
    assert runs[0].words == 7
    # short Latin terms (units, names) are not runs
    assert foreign_runs(hi + " NASA km", "Latin") == []
    # URLs / code are protected
    t = hi + " see https://example.com/a/b/c and `print the value here`"
    assert foreign_runs(t, "Latin", exclude=find_protected(t)) == []


def test_char_script_common():
    assert char_script("।") == "Other"  # danda has Script=Common, shared by many Indic scripts
    assert char_script("a") == "Latin"


def test_protected_regions_kinds():
    t = "Text `x = 1` and $E = mc^2$ and $$\\int_0^1 x\\,dx$$ see [docs](https://a.b/c) or https://x.org/y.\n```python\nprint(1)\n```\n"
    kinds = [r.kind for r in find_protected(t)]
    assert kinds == ["code_inline", "math_inline", "math_display", "link_target", "url", "code_block"]
    masked = mask_protected(t)
    assert len(masked) == len(t) and "mc^2" not in masked and "print" not in masked


def test_currency_is_not_math():
    assert find_protected("It costs $5 and $10 today.") == []


def test_latex_environments_and_brackets():
    t = r"A \(a+b\) B \[x^2\] C \begin{align} y &= 2 \end{align}"
    assert [r.kind for r in find_protected(t)] == ["math_inline", "math_display", "math_env"]


def test_sentences_with_danda_question_and_newlines():
    hi = FX["translations"]["hi"]["translation"]
    t = hi + " " + hi + "\nSecond line? Yes! Pi is 3.14 exactly."
    spans = sentences(t)
    got = [t[s:e] for s, e in spans]
    assert got == [hi, hi, "Second line?", "Yes!", "Pi is 3.14 exactly."]


def test_urdu_full_stop_and_protected_dots_not_split():
    t = "a۔ b۔ Visit https://x.org/a.b now. Math $1. 2$ ok."
    got = [t[s:e] for s, e in sentences(t)]
    assert got == ["a۔", "b۔", "Visit https://x.org/a.b now.", "Math $1. 2$ ok."]


def test_clauses():
    t = "First part, second part; third: end."
    assert [t[s:e] for s, e in clauses(t)] == ["First part,", "second part;", "third:", "end."]


def test_markdown_table_rows_are_segments():
    t = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert len(sentences(t)) == 3


def test_span_overlap_and_iou():
    assert overlaps((0, 5), (4, 9)) and not overlaps((0, 5), (5, 9))
    # zero-width omission point (incl. 1-char space adjacency)
    assert overlaps((3, 3), (0, 5)) and overlaps((5, 5), (0, 5)) and overlaps((6, 6), (0, 5)) and not overlaps((7, 7), (0, 5))
    assert iou((0, 10), (5, 15)) == pytest.approx(5 / 15)
    assert iou((0, 4), (0, 4)) == 1.0 and iou((0, 1), (2, 3)) == 0.0
    assert overlaps((1.0, 2.5), (2.0, 4.0))  # seconds work the same way
    assert union_span([(3, 5), None, (1, 2)]) == (1, 5)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("number", "number_unit"), ("Number or unit", "number_unit"), ("Term-Entity", "term_entity"),
        ("Wrong language or script", "wrong_language_script"), ("untranslated", "wrong_language_script"),
        ("Omission", "omission"), ("speech omission", "speech_omission_truncation"), ("truncation", "speech_omission_truncation"),
        ("Repetition", "speech_repetition"), ("garbled audio", "speech_garbled"), ("audio_noise", "speech_garbled"),
        ("hallucination", "addition"), ("LaTeX", "format"), ("translation_omission", "omission"),
    ],
)
def test_category_aliases(raw, expected):
    assert canonical_category(raw) == expected


def test_unknown_category_never_raises():
    assert canonical_category("Politeness") == "unmapped:politeness"
    assert all(canonical_category(c) == c for c in CATEGORIES)


def test_severity_aliases():
    assert canonical_severity("Major") == "major" and canonical_severity("high") == "critical"
    assert canonical_severity(None) is None and canonical_severity("weird") is None


def test_label_span_frames():
    assert _parse_label({"type": "number", "severity": "major", "span": [14, 20]}).frame == "chars"
    assert _parse_label({"type": "repetition", "span": [1.5, 2.0]}).frame == "seconds"
    assert _parse_label({"type": "repetition", "span": [1, 2]}).frame == "chars_or_seconds"
    assert _parse_label({"type": "truncation", "span": [3, 7], "span_unit": "s"}).frame == "seconds"
    lab = _parse_label({"type": "Term", "span": [0, 4], "field": "Translation", "annotator": "x"})
    assert lab.category == "term_entity" and lab.target_field == "translation" and lab.extra == {"annotator": "x"}
    with pytest.raises(ValueError):
        _parse_label({"type": "number", "span": [5, 2]})
    with pytest.raises(ValueError):
        _parse_label({"type": "number", "span": [True, 2]})
