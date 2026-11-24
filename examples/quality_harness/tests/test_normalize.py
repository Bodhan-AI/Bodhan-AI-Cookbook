import json
from pathlib import Path

import pytest

from bodhan_eval.normalize import normalize

FIXTURES = Path(__file__).parent / "fixtures" / "bodhan"


@pytest.mark.parametrize(
    "native",
    ["२५", "২৫", "௨௫", "੨੫", "૨૫", "୨୫", "౨౫", "೨೫", "൨൫", "٢٥", "۲۵", "２５"],  # Devanagari … full-width
)
def test_native_digits_become_ascii(native):
    assert normalize(f"मूल्य {native} है").text == "मूल्य 25 है"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1,00,000", "100000"),
        ("12,34,567", "1234567"),
        ("100,000", "100000"),
        ("1,000,000.50", "1000000.50"),
        ("₹१,००,०००", "₹100000"),  # native digits first, then grouping
        ("10,20,30", "10,20,30"),  # a list, not a grouped number
        ("2019,2020", "2019,2020"),
        ("1,2,3", "1,2,3"),
    ],
)
def test_digit_group_commas(raw, expected):
    assert normalize(raw).text == expected


@pytest.mark.parametrize(
    "a, b",
    [
        ("क़", "क़"),  # Devanagari qa: precomposed vs ka + nukta
        ("ো", "ো"),  # Bengali o: precomposed vs two-part vowel
        ("ஔ", "ஔ"),  # Tamil au
        ("क्‍ष", "क्ष"),  # zero-width joiner
        ("ना​म", "नाम"),  # zero-width space
        ("﻿नमस्ते", "नमस्ते"),  # BOM
        ("হঠাৎ", "হঠাত্‍"),  # Bengali khanda ta, atomic vs virama + ZWJ
        ("അവൻ", "അവന്‍"),  # Malayalam chillu n, atomic vs old encoding
    ],
)
def test_unicode_variants_compare_equal(a, b):
    assert normalize(a).text == normalize(b).text


def test_punctuation_and_whitespace():
    assert normalize("  “नमस्ते”  — कैसे हैं?।\n\nठीक…  ").text == '"नमस्ते" - कैसे हैं?. ठीक...'
    assert normalize("کیا؟ ہاں۔").text == "کیا? ہاں."


def test_default_keeps_case_and_letters():
    assert normalize("Dr. Meera Rao").text == "Dr. Meera Rao"
    assert normalize("ज़रूर").text != normalize("जरूर").text  # nukta kept by default


def test_loose_folds_for_speech_comparison():
    assert normalize("ज़रूर", loose=True).text == normalize("जरूर", loose=True).text
    assert normalize("হাঁস", loose=True).text == "হাংস"  # candrabindu -> anusvara (Bengali)
    assert normalize("Hello, World! 100 °C (approx.)", loose=True).text == "hello world 100 c approx"


def test_loose_matches_live_transcript_apart_from_numbers_and_abbreviations():
    spoken = json.loads((FIXTURES / "translate_hi.json").read_text())["choices"][0]["message"]["content"]
    heard = json.loads((FIXTURES / "transcribe_hi.json").read_text())["text"]
    a, b = normalize(spoken, loose=True).text.split(), normalize(heard, loose=True).text.split()
    # What is left after punctuation goes is exactly the numbers and "डॉ" (spoken in full as डॉक्टर),
    # which the speech alignment treats as wildcards.
    assert set(a) - set(b) == {"100", "डॉ", "5", "2023"}


@pytest.mark.parametrize(
    "text",
    ["हिंदी १,००,००० रुपये।", "  “Hi”…  there ", "ज़‍रूर হঠাৎ അവൻ", "Hello, World! 100 °C", "", "   "],
)
@pytest.mark.parametrize("loose", [False, True])
def test_idempotent(text, loose):
    once = normalize(text, loose=loose).text
    assert normalize(once, loose=loose).text == once


def test_spans_map_back_to_original():
    original = "​पानी १,००,००० लीटर।  ठीक…"
    n = normalize(original)
    assert n.text == "पानी 100000 लीटर. ठीक..."

    def back(sub):
        start = n.text.index(sub)
        s, e = n.to_original(start, start + len(sub))
        return original[s:e]

    assert back("पानी") == "पानी"  # shifted by the removed zero-width space
    assert back("100000") == "१,००,०००"  # digits mapped and commas removed
    assert back("लीटर.") == "लीटर।"
    assert back("...") == "…"  # one original char expanded to three
    assert back("ठीक...") == "ठीक…"


def test_spans_through_composition_and_insertion_points():
    original = "क़लम"  # ka + nukta (NFC keeps it decomposed) then "lam"
    n = normalize(original)
    assert n.to_original(0, len(n.text)) == (0, len(original))
    assert n.to_original(0, 0) == (0, 0)
    assert n.to_original(len(n.text), len(n.text)) == (len(original), len(original))

    bengali = "কো"  # ka + two-part vowel composes into one character
    nb = normalize("কো")
    assert nb.text == bengali and nb.to_original(1, 2) == (1, 3)
