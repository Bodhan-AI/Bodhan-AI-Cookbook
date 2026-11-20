import unicodedata

import pytest
from conftest import fixture_rows

from bodhan_eval.normalize import ascii_digits, mask_protected, normalize, protected_spans, script_of_char, sentence_spans, to_original


@pytest.mark.parametrize("digits,expected", [
    ("१००", "100"),  # Devanagari
    ("৭২", "72"),  # Bengali
    ("௨௯", "29"),  # Tamil
    ("١٢", "12"),  # Arabic-Indic
    ("۲۵", "25"),  # Extended Arabic-Indic (Urdu)
    ("૩", "3"),  # Gujarati
    ("೧೪", "14"),  # Kannada
])
def test_ascii_digits_any_script_keeps_length(digits, expected):
    assert ascii_digits(digits) == expected and len(ascii_digits("x" + digits)) == len(digits) + 1


def test_normalize_equates_variants_and_maps_offsets():
    precomposed, decomposed = "क़", "क़"  # QA with nukta, two encodings
    assert normalize(precomposed)[0] == normalize(decomposed)[0]
    text = "A‍b  “C” २३"
    clean, idx = normalize(text)
    assert clean == 'ab "c" 23'
    s = clean.index("23")
    assert to_original((s, s + 2), idx, text) == (len(text) - 2, len(text))
    assert to_original((1, 2), idx, text) == (2, 3)  # "b" sits after the zero-width joiner


def test_to_original_covers_combining_marks():
    text = "क़ा x"  # QA + nukta + vowel sign, then " x"
    clean, idx = normalize(text)
    assert to_original((0, len(clean) - 2), idx, text) == (0, 3)


def test_protected_spans():
    text = "Use $E = mc^2$ and `pip install x` at https://x.org/a <b>now</b>.\n```\ncode $y$\n```\n"
    kinds = [k for _, _, k in protected_spans(text)]
    assert kinds == ["imath", "code", "url", "html", "html", "fence"]
    assert protected_spans("It costs $5 and $10 today.") == []
    masked = mask_protected(text)
    assert len(masked) == len(text) and "mc^2" not in masked and masked.count("\n") == text.count("\n")


def test_script_of_char():
    assert [script_of_char(c) for c in "aकाகকا 1."] == ["LATIN", "DEVANAGARI", "DEVANAGARI", "TAMIL", "BENGALI", "ARABIC",
                                                                                   None, None, None]


def test_sentence_spans_on_real_hindi_and_markdown():
    rows = fixture_rows()
    hi = rows["t02"]["translation"]
    spans = sentence_spans(hi)
    assert len(spans) == 3 and all(hi[e - 1] == "।" for _, e in spans)
    md = rows["t07"]["source"]
    lines = [md[s:e] for s, e in sentence_spans(md)]
    assert "| Iron | 7.87 |" in lines  # decimals are not sentence ends; table rows are units
    assert lines[-2:] == ["Einstein showed that $E = mc^2$, where $c$ is the speed of light."] or len(lines) >= 6


def test_all_fixture_text_survives_nfc():
    for r in fixture_rows().values():
        if r.get("translation"):
            assert unicodedata.is_normalized("NFC", normalize(r["translation"])[0])
