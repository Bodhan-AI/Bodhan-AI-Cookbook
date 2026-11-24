import pytest

from bodhan_eval.checks.translation import (
    check_numbers,
    check_script,
    check_terms,
    length_signals,
    source_entities,
)
from bodhan_eval.languages import load_languages
from bodhan_eval.params import Params

L = load_languages()
P = Params.defaults()
SRC = "Water boils at 100 °C at sea level."
HI = "समुद्र तल पर पानी 100 °C पर उबलता है।"


def cats(res):
    return [f.category for f in res.flags]


# --- script ---------------------------------------------------------------------------


def test_script_clean_hindi_tamil_bengali():
    assert check_script(SRC, HI, L["hi"], P).flags == []
    ta = "நீர் கடல் மட்டத்தில் 100 °C இல் கொதிக்கிறது."
    assert check_script(SRC, ta, L["ta"], P).flags == []
    bn = "সমুদ্রপৃষ্ঠে জল ১০০ °C তাপমাত্রায় ফোটে।"
    assert check_script(SRC, bn, L["bn"], P).flags == []


def test_script_wrong_language_whole_text():
    res = check_script(SRC, HI, L["ta"], P)  # Hindi delivered for a Tamil request
    assert cats(res) == ["wrong_language"]
    f = res.flags[0]
    assert f.severity == "critical" and f.span == (0, len(HI))
    assert "Deva" in f.evidence


def test_script_identical_to_source():
    res = check_script(SRC, SRC, L["hi"], P)
    assert cats(res) == ["wrong_language"] and "identical" in res.flags[0].evidence


def test_script_one_sentence_left_untranslated_is_localized():
    src = "Water boils at 100 °C at sea level. The Earth orbits the Sun once every year."
    tr = "সমুদ্রপৃষ্ঠে জল ১০০ °C তাপমাত্রায় ফোটে। The Earth orbits the Sun once every year."
    res = check_script(src, tr, L["bn"], P)
    assert cats(res) == ["wrong_language"]
    s, e = res.flags[0].span
    assert tr[s:e] == "The Earth orbits the Sun once every year."
    assert "untranslated" in res.flags[0].evidence


def test_script_ignores_names_code_and_math():
    src = "Use `print()` in Python to compute $x^2 + y^2$ as shown by NASA."
    tr = "NASA के अनुसार $x^2 + y^2$ की गणना करने के लिए Python में `print()` का उपयोग करें।"
    assert check_script(src, tr, L["hi"], P).flags == []


def test_script_accepts_alternate_registered_script():
    # Sindhi is registered with both Devanagari and Arabic scripts.
    assert check_script("Hello friend.", "سلام دوست", L["sd"], P).flags == []


# --- numbers --------------------------------------------------------------------------


def test_numbers_clean_with_indic_digits():
    assert check_numbers(SRC, "समुद्र तल पर पानी १०० °C पर उबलता है।", L["hi"], P).flags == []


def test_numbers_changed_digit_is_localized():
    tr = "समुद्र तल पर पानी 10 °C पर उबलता है।"
    res = check_numbers(SRC, tr, L["hi"], P)
    assert cats(res) == ["number"]
    f = res.flags[0]
    s, e = f.span
    assert tr[s:e] == "10" and f.confidence >= 0.9
    assert SRC[f.source_span[0] : f.source_span[1]] == "100"


def test_numbers_missing_number():
    src = "The bridge is 25 metres long and 4 metres wide."
    tr = "पुल 25 मीटर लंबा और चौड़ा है।"
    res = check_numbers(src, tr, L["hi"], P)
    assert cats(res) == ["number"] and res.flags[0].details["expected"] == "4"


def test_numbers_unit_swap():
    tr = "समुद्र तल पर पानी 100 °F पर उबलता है।"
    res = check_numbers(SRC, tr, L["hi"], P)
    assert cats(res) == ["number"]
    assert "°F" in res.flags[0].evidence and res.flags[0].confidence >= 0.85


def test_numbers_grouping_and_number_words_are_fine():
    src = "About 1,000,000 people live there, and two rivers meet."
    tr = "वहाँ लगभग 10,00,000 लोग रहते हैं, और 2 नदियाँ मिलती हैं।"
    assert check_numbers(src, tr, L["hi"], P).flags == []


def test_numbers_ordinals_are_low_confidence():
    src = "It was the 1st attempt."
    tr = "यह पहला प्रयास था।"
    res = check_numbers(src, tr, L["hi"], P)
    assert all(f.confidence < 0.3 for f in res.flags)


def test_numbers_currency_vs_latex():
    src = "It costs $5. The area is $\\pi r^2$."
    tr = "इसकी कीमत $5 है। क्षेत्रफल $\\pi r^2$ है।"
    assert check_numbers(src, tr, L["hi"], P).flags == []


# --- terms ----------------------------------------------------------------------------


def test_terms_url_and_code_preserved():
    src = "See https://example.org/docs and run `make test`."
    tr = "https://example.org/docs देखें और `make test` चलाएँ।"
    assert check_terms(src, tr, L["hi"], P).flags == []


def test_terms_url_altered_is_localized():
    src = "See https://example.org/docs for details."
    tr = "विवरण के लिए https://example.org/doc देखें।"
    res = check_terms(src, tr, L["hi"], P)
    assert cats(res) == ["term"]
    s, e = res.flags[0].span
    assert "example.org" in tr[s:e]


def test_terms_invented_latin_name():
    src = "Paris is the capital of France."
    tr = "London फ्रांस की राजधानी है।"
    res = check_terms(src, tr, L["hi"], P)
    assert cats(res) == ["term"] and tr[slice(*res.flags[0].span)] == "London"


def test_terms_kept_names_and_inflections_are_fine():
    src = "NASA and Google built new computers."
    tr = "NASA और Google ने नए computers बनाए।"
    assert check_terms(src, tr, L["hi"], P).flags == []


def test_source_entities():
    ents = [
        e
        for e, _ in source_entities(
            "The Indian Space Research Organisation launched it. DNA is old."
        )
    ]
    assert "Indian Space Research Organisation" in ents and "DNA" in ents


def test_length_signals():
    sig = length_signals("Hello world.", "नमस्ते दुनिया।")
    assert sig["source_sentences"] == 1 and sig["char_ratio"] > 0


@pytest.mark.parametrize("code", ["te", "ml", "kn", "gu", "pa", "or", "ur", "sat"])
def test_script_check_runs_for_unseen_languages(code):
    # Not demoed languages: the check must work from config alone (Hindi text => wrong).
    res = check_script(SRC, HI, L[code], P)
    assert cats(res) == ["wrong_language"]


def test_digits_inside_a_source_word_are_not_extra_numbers():
    src = "Twenty20 competitions will be part of the 2028 Olympics."
    tr = "ट्वेंटी 20 प्रतियोगिताएं 2028 ओलंपिक का हिस्सा होंगी।"
    assert check_numbers(src, tr, L["hi"], P).flags == []
