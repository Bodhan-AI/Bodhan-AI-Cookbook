"""Must-catch / must-not-flag tables for ``numbers`` and ``length`` (TESTING.md §2).

Numbers are deterministic proof and must flag alone. Unit and month changes are visible only through
the back-translation, so those rows run the real chain (back_translation -> numbers) against the stub.
Length is a weak hint by design (max 0.35): it must *signal* an outlier but never flag on its own.
"""

from __future__ import annotations

import math

import pytest

from bodhan_eval.checks.deterministic import LengthCheck
from bodhan_eval.checks.roundtrip import BackTranslationCheck, NumberCheck

DIGIT_ZEROS = {
    "devanagari": 0x0966, "bengali": 0x09E6, "gurmukhi": 0x0A66, "gujarati": 0x0AE6, "odia": 0x0B66,
    "tamil": 0x0BE6, "telugu": 0x0C66, "kannada": 0x0CE6, "malayalam": 0x0D66,
    "arabic_indic": 0x0660, "extended_arabic_indic": 0x06F0,
}  # fmt: skip


def native(zero: int, number: str) -> str:
    return "".join(chr(zero + int(d)) if d.isdigit() else d for d in number)


# ---------------------------------------------------------------------------
# numbers, deterministic rows
# ---------------------------------------------------------------------------

NUMBER_CATCH = [
    pytest.param("The train leaves at 7 pm.", "ट्रेन शाम 8 बजे निकलती है।", "8", id="digit-changed"),
    pytest.param("The value of pi is about 3.14.", "पाई का मान लगभग 3.41 है।", "3.41", id="decimal-changed"),
    pytest.param("Add 2 spoons of sugar and 3 cups of milk.", "चीनी के चम्मच और 3 कप दूध डालें।", None, id="number-dropped"),
    pytest.param("Add sugar to the milk.", "दूध में 2 चम्मच चीनी डालें।", "2", id="number-added"),
    pytest.param("The city has 1,00,000 people.", "शहर में 10,000 लोग हैं।", "10,000", id="indian-grouping-changed"),
    pytest.param("It is accurate to 2 significant figures: 9.8 m/s².", "இது 9.8 மீ/வி² ஆகும்.", None,
                 id="number-dropped-while-the-exponent-stays"),  # seen live (ta): the ² must not stand in for the 2
    pytest.param("Vitamin B12 is found in milk.", "विटामिन बी 13 दूध में पाया जाता है।", "13",
                 id="glued-number-changed-when-written-apart"),
]  # fmt: skip

NUMBER_CLEAN = [
    pytest.param("There are 25 students in the class.", "कक्षा में २५ छात्र हैं।", id="devanagari-digits"),
    pytest.param("The city has 100,000 people.", "शहर में 1,00,000 लोग हैं।", id="indian-vs-western-grouping"),
    pytest.param("She has three books.", "उसके पास 3 किताबें हैं।", id="source-word-target-digit"),
    pytest.param("Water is H2O and plants absorb CO2.", "पानी H2O है और पौधे CO2 अवशोषित करते हैं।", id="formulas"),
    pytest.param("1. Boil water.\n2. Add salt.", "1. पानी उबालें।\n2. नमक डालें।", id="list-numbering"),
    pytest.param("Water boils at 100 °C.", "জল ১০০ °C তাপমাত্রায় ফোটে।", id="bengali-digits"),
    pytest.param("It costs 3.50 rupees.", "इसकी कीमत 3.5 रुपये है।", id="trailing-zero-decimal"),
    pytest.param("The year 2024 had 366 days.", "वर्ष 2024 में 366 दिन थे।", id="several-numbers-kept"),
    pytest.param("Gravity is 9.8 m/s² to 2 significant figures.", "মাধ্যাকর্ষণ ২ টি সংখ্যা পর্যন্ত ৯.৮ মি/সেকেন্ড২।",
                 id="exponent-written-as-a-glued-digit"),  # seen live (bn)
    pytest.param("| B1 | beriberi |\n| B12 | anemia |", "| பி1 | பெரிபெரி |\n| பி12 | இரத்த சோகை |",
                 id="digits-glued-to-indic-letters"),  # seen live (ta, bn)
    pytest.param("| B1 | beriberi |\n| B12 | anemia |", "| बी 1 | बेरीबेरी |\n| बी 12 | एनीमिया |",
                 id="glued-in-source-apart-in-translation"),  # seen live (hi)
    pytest.param("Plants absorb CO₂ and release O₂.", "पौधे CO2 अवशोषित करते हैं और O2 छोड़ते हैं।",
                 id="subscript-digits"),
]  # fmt: skip


@pytest.mark.parametrize(("source", "translation", "flagged_text"), NUMBER_CATCH)
def test_numbers_must_catch(make_ctx, run_one, fuse_item, source, translation, flagged_text) -> None:
    r = run_one(NumberCheck, make_ctx(), source, translation)
    flags, _ = fuse_item(r)
    hits = [f for f in flags if f.category == "number"]
    assert hits, [s.evidence for s in r.signals]
    if flagged_text:
        assert any(f.text == flagged_text for f in hits), [f.text for f in hits]


@pytest.mark.parametrize(("source", "translation"), NUMBER_CLEAN)
def test_numbers_must_not_flag(make_ctx, run_one, fuse_item, source, translation) -> None:
    r = run_one(NumberCheck, make_ctx(), source, translation)
    assert fuse_item(r) == ([], []), [s.evidence for s in r.signals]


@pytest.mark.parametrize("zero", DIGIT_ZEROS.values(), ids=DIGIT_ZEROS.keys())
def test_numbers_every_digit_system_matches_ascii(make_ctx, run_one, fuse_item, zero: int) -> None:
    r = run_one(
        NumberCheck,
        make_ctx(),
        "The answer is 2509 and 1,00,000 and 3.75.",
        f"उत्तर {native(zero, '2509')} और {native(zero, '1,00,000')} और {native(zero, '3.75')} है।",
    )
    assert fuse_item(r) == ([], []), [s.evidence for s in r.signals]


# ---------------------------------------------------------------------------
# numbers through the back-translation (units, months, numbers written in words)
# ---------------------------------------------------------------------------

BT_NUMBER_CATCH = [
    pytest.param("The road is 5 km long.", "सड़क 5 मीटर लंबी है।", {"सड़क 5 मीटर लंबी है।": "The road is 5 meters long."}, id="km-became-metres"),
    pytest.param("The exam is on 5 March.", "परीक्षा 5 अप्रैल को है।", {"परीक्षा 5 अप्रैल को है।": "The exam is on 5 April."}, id="month-changed"),
    pytest.param("The tank holds 20 litres of water.", "टैंक में 20 किलोग्राम पानी आता है।", {"टैंक में 20 किलोग्राम पानी आता है।": "The tank holds 20 kilograms of water."}, id="unit-family-swap"),
]  # fmt: skip

BT_NUMBER_CLEAN = [
    pytest.param("Mix 3 cups of flour with water.", "आटे के तीन कप पानी में मिलाएँ।", {"आटे के तीन कप पानी में मिलाएँ।": "Mix three cups of flour in water."}, id="number-in-words-confirmed-by-bt"),
    pytest.param("The road is 5 km long.", "सड़क 5 किलोमीटर लंबी है।", {"सड़क 5 किलोमीटर लंबी है।": "The road is 5 kilometres long."}, id="unit-spelled-out"),
    pytest.param("The exam is on 5 March.", "परीक्षा 5 मार्च को है।", {"परीक्षा 5 मार्च को है।": "The exam is on March 5."}, id="date-order-changed"),
]  # fmt: skip


@pytest.mark.parametrize(("source", "translation", "bt"), BT_NUMBER_CATCH)
def test_numbers_via_back_translation_must_catch(
    start_stub, make_ctx, run_chain, fuse_item, source, translation, bt
) -> None:
    ctx = make_ctx(start_stub({"translate": {"en": bt}}))
    r = run_chain([BackTranslationCheck, NumberCheck], ctx, source, translation)
    flags, _ = fuse_item(r)
    assert [f.category for f in flags] == ["number"], [s.evidence for s in r.signals]


@pytest.mark.parametrize(("source", "translation", "bt"), BT_NUMBER_CLEAN)
def test_numbers_via_back_translation_must_not_flag(
    start_stub, make_ctx, run_chain, fuse_item, source, translation, bt
) -> None:
    ctx = make_ctx(start_stub({"translate": {"en": bt}}))
    r = run_chain([BackTranslationCheck, NumberCheck], ctx, source, translation)
    assert r.checks["back_translation"]["status"] == "ok"
    assert fuse_item(r) == ([], []), [s.evidence for s in r.signals]


# ---------------------------------------------------------------------------
# length (weak hint only)
# ---------------------------------------------------------------------------

SOURCE = "Plants make their own food using sunlight, water and carbon dioxide from the air around them."
NORMAL = "पौधे सूर्य के प्रकाश, पानी और अपने आसपास की हवा से कार्बन डाइऑक्साइड का उपयोग करके अपना भोजन स्वयं बनाते हैं।"


def length_ctx(make_ctx, median_ratio: float = 1.1, sd: float = 0.12, n: int = 30):
    ctx = make_ctx()
    ctx.stats.length_ratio["hi"] = (math.log(median_ratio), sd, n)
    return ctx


@pytest.mark.parametrize(
    ("translation", "category"),
    [
        pytest.param("पौधे भोजन बनाते हैं।", "omission", id="far-too-short"),
        pytest.param(NORMAL + " " + NORMAL + " " + NORMAL, "addition", id="far-too-long"),
    ],
)
def test_length_must_catch_as_a_weak_hint(make_ctx, run_one, fuse_item, translation: str, category: str) -> None:
    r = run_one(LengthCheck, length_ctx(make_ctx), SOURCE, translation)
    assert [(s.category, s.score <= 0.35) for s in r.signals] == [(category, True)]
    assert fuse_item(r)[0] == []  # never flags on its own


def test_length_must_not_flag_normal_variance(make_ctx, run_one) -> None:
    r = run_one(LengthCheck, length_ctx(make_ctx), SOURCE, NORMAL)
    assert r.signals == [] and abs(r.metrics["length_z"]) < 3


def test_length_language_with_too_few_items_uses_the_prior(make_ctx, run_one) -> None:
    ctx = make_ctx()  # no per-language statistics: the prior from langs.json (ratio 1.05, sd 0.35) applies
    r = run_one(LengthCheck, ctx, SOURCE, NORMAL[: int(len(NORMAL) * 0.8)])
    assert r.signals == []


def test_length_skips_short_sources(make_ctx, run_one) -> None:
    r = run_one(LengthCheck, make_ctx(), "Hi.", "नमस्ते।")
    assert r.checks["length"] == {"status": "skipped", "signals": 0, "reason": "source too short for a length ratio"}
