import pytest

from bodhan_eval.checks import (
    canonical, check_entities, check_format, check_numbers, check_script, entities, length_outliers,
    number_words, numbers, reference_scores, run_checks,
)
from bodhan_eval.langs import expected_scripts, script_counts

SRC = "Water boils at 100 °C. Dr Meera Rao measured it on 5 March 2023."
HI = "समुद्र तल पर पानी 100 डिग्री सेल्सियस पर उबलता है। डॉ मीरा राव ने इसे 5 मार्च 2023 को मापा था।"
BT = "Water boils at 100 degrees Celsius at sea level. Dr Meera Rao measured it on March 5, 2023."


def kinds(candidates):
    return [(c.category, c.evidence.get("kind"), c.standalone) for c in candidates]


# --- languages and scripts

@pytest.mark.parametrize("text, script", [("नमस्ते", "DEVANAGARI"), ("নমস্কার", "BENGALI"), ("வணக்கம்", "TAMIL"),
                                          ("ᱥᱟᱱᱛᱟᱲᱤ", "OL CHIKI"), ("ꯃꯤꯇꯩ", "MEETEI MAYEK"), ("سلام", "ARABIC")])
def test_script_of_any_script(text, script):
    assert set(script_counts(text + " 5।")) == {script}  # digits and danda are not letters


def test_expected_scripts():
    assert expected_scripts("hi") == {"DEVANAGARI"} and expected_scripts("mni-Beng") == {"BENGALI"}
    assert expected_scripts("sd") == {"ARABIC", "DEVANAGARI"}  # ambiguous code: accept both
    assert expected_scripts("xx", ["తెలుగు Hello", "తెలుగు", "नमस्ते"]) == {"TELUGU"}  # unseen code: dataset majority
    assert expected_scripts("xx") is None


# --- 1. script

def test_script_clean_with_names_kept_in_latin():
    src = "Dr Meera Rao uses Python and NASA data. The results were good."
    assert check_script(src, "डॉ मीरा राव Python और NASA डेटा का उपयोग करती हैं। परिणाम अच्छे थे।", {"DEVANAGARI"}) == []
    assert check_script(src, "Dr Meera Rao Python NASA का उपयोग करती हैं।", {"DEVANAGARI"}) == []


def test_script_untranslated_sentence_and_whole_item():
    src = "Dr Meera Rao uses Python and NASA data. The results were good."
    partial = check_script(src, "डॉ मीरा राव Python और NASA डेटा का उपयोग करती हैं। The results were good.", {"DEVANAGARI"})
    assert kinds(partial) == [("wrong_language", "untranslated", True)] and partial[0].severity == "major"
    whole = check_script(src, src, {"DEVANAGARI"})
    assert len(whole) == 2 and {c.severity for c in whole} == {"critical"}


def test_script_other_language_and_latin_runs():
    hindi_for_tamil = check_script("Hello there.", "नमस्ते वहाँ।", {"TAMIL"})
    assert kinds(hindi_for_tamil) == [("wrong_language", "other_script", True)]
    mixed = check_script("Results.", "डॉ मीरा राव data science का उपयोग करती हैं।", {"DEVANAGARI"})
    assert kinds(mixed) == [("wrong_language", "untranslated", False)] and mixed[0].evidence["text"] == "data science"


def test_script_ignores_code_math_and_urls():
    tr = "यह देखें `print hello world` और https://example.org/some/path और $x + y$।"
    assert check_script("See this.", tr, {"DEVANAGARI"}) == []


# --- 2. numbers

def test_number_extraction():
    assert canonical("05") == "5" and canonical("1000000.50") == "1000000.5" and canonical("0.0") == "0"
    assert [v for v, _ in numbers("On 2023-05-01 at 10:30, ₹१,००,००० and 3.5%")] == ["2023", "5", "1", "10", "30", "100000", "3.5"]
    assert [v for v, _ in numbers("Code `x = 7` and $a^2$ and https://x.org/9 skipped, 4 kept")] == ["4"]
    text = "कीमत ₹१,००,००० है"
    (value, (s, e)), = numbers(text)
    assert value == "100000" and text[s:e] == "१,००,०००"


def test_number_words():
    assert number_words("two thousand and five, fifty, twenty-one, the fifth, ninety two percent, one hundred and one") == \
        ["2005", "50", "21", "5", "92", "101"]
    assert number_words("twenty twenty three") == ["20", "23"]
    assert number_words("three lakh people and 2 crore") == ["300000"]
    assert number_words("no numbers here") == []


def test_numbers_clean_and_spelled_out():
    assert check_numbers(SRC, HI, BT) == []
    assert check_numbers("Entry costs 50 rupees.", "प्रवेश शुल्क पचास रुपये है।", "The entry fee is fifty rupees.") == []
    assert check_numbers("Five apples.", "5 सेब।", "5 apples.") == []  # source wrote it in words
    assert check_numbers("Costs ₹1,00,000.", "कीमत ₹१,००,००० है।", None) == []


def test_numbers_changed_missing_extra():
    changed = check_numbers(SRC, HI.replace("2023", "2032"), BT.replace("2023", "2032"))
    assert kinds(changed) == [("number", "changed", True)]
    assert changed[0].evidence["source"] == "2023" and changed[0].evidence["translation"] == "2032"
    assert HI.replace("2023", "2032")[slice(*changed[0].span)] == "2032"

    missing = check_numbers("Entry costs 50 rupees.", "प्रवेश शुल्क रुपये है।", "The entry fee is rupees.")
    assert kinds(missing) == [("number", "missing", True)] and missing[0].side == "source"
    assert kinds(check_numbers("Entry costs 50 rupees.", "प्रवेश शुल्क रुपये है।", None)) == [("number", "missing", False)]

    extra = check_numbers("Entry is free.", "प्रवेश 20 रुपये है।", "Entry is 20 rupees.")
    assert kinds(extra) == [("number", "extra", False)]


def test_units():
    assert check_numbers(SRC, HI, BT) == []  # °C spelled out as डिग्री सेल्सियस, back-translated as Celsius
    miles = check_numbers("It is 5 km away.", "यह 5 मील दूर है।", "It is 5 miles away.")
    assert kinds(miles) == [("number", "unit_missing", True)] and miles[0].evidence["unit"] == "km"
    assert check_numbers("Growth was 5%.", "वृद्धि 5% थी।", None) == []


# --- 3. format

MD = ("## Results\n\n| Metric | Value |\n|---|---|\n| Accuracy | 92% |\n\nUse `print` and see [docs](https://x.org). "
      "The **fast** model: $E = mc^2$.\n\n\\begin{align}a\\end{align}\n\n```\nx = 1\n```")
TA = ("## முடிவுகள்\n\n| அளவீடு | மதிப்பு |\n|---|---|\n| துல்லியம் | 92% |\n\n`print` ஐப் பயன்படுத்தி [ஆவணங்கள்](https://x.org) "
      "பார்க்கவும். **வேகமான** மாதிரி: $E = mc^2$.\n\n\\begin{align}a\\end{align}\n\n```\nx = 1\n```")


def test_format_clean_when_text_is_translated_and_structure_kept():
    assert check_format(MD, TA) == []


@pytest.mark.parametrize(
    "broken, kind, severity",
    [
        (TA.replace("| துல்லியம் | 92% |", "| துல்லியம் 92% |"), "table_row", "major"),
        (TA.replace("$E = mc^2$", "$E = mc^3$"), "math", "major"),
        (TA.replace("$E = mc^2$", "E = mc^2"), "math", "major"),
        (TA.replace("https://x.org", "https://y.org"), "link_url", "minor"),
        (TA.replace("**வேகமான**", "**வேகமான"), "emphasis", "minor"),
        (TA.replace("## ", ""), "heading", "minor"),
        (TA.replace("x = 1", "x = 2"), "code_block", "major"),
        (TA.replace("`print`", "print"), "inline_code", "major"),
        (TA.replace("\\end{align}", ""), "latex_env", "major"),
    ],
)
def test_format_breaks_are_found_and_located(broken, kind, severity):
    found = check_format(MD, broken)
    assert [(c.evidence["kind"], c.severity, c.standalone) for c in found][:1] == [(kind, severity, True)]


def test_format_span_points_at_the_broken_element():
    broken = TA.replace("| துல்லியம் | 92% |", "| துல்லியம் 92% |")
    (c,) = check_format(MD, broken)
    assert broken[slice(*c.span)] == "| துல்லியம் 92% |"
    no_heading = TA.replace("## ", "")
    (gone,) = check_format(MD, no_heading)
    assert gone.span == (0, len(no_heading))  # missing: the gap between its neighbours; the only heading, so all of it


def test_format_ignores_markup_inside_code():
    src, tgt = "```\n## not a heading | a | b |\n```", "```\n## not a heading | a | b |\n```"
    assert check_format(src, tgt) == []


# --- 4. length

def test_length_outliers_are_relative_to_the_language():
    pairs: dict[object, tuple[str, str]] = {k: ("A sentence of moderate length here.", "एक मध्यम लंबाई का वाक्य यहाँ है।") for k in range(8)}
    pairs["short"] = ("A sentence of moderate length here. And another long sentence follows it here.", "एक वाक्य।")
    out = length_outliers(pairs)
    assert list(out) == ["short"] and out["short"].category == "omission" and not out["short"].standalone
    assert length_outliers(dict(list(pairs.items())[:4])) == {}  # too few items to judge


# --- 5. reference

def test_reference_scores():
    same = reference_scores(HI, HI)
    assert same["chrf_pp"] == 100.0 and same["bleu"] == 100.0
    assert reference_scores(HI, "बिलकुल अलग वाक्य।")["chrf_pp"] < 20


# --- 6. names and terms

def test_entities():
    assert [e for e, _ in entities(SRC)] == ["Dr Meera Rao", "March"]
    text = "The Eiffel Tower is in Paris. NASA and COVID-19 and iPhone. In India the GDP grew. The Reserve Bank of India."
    assert [e for e, _ in entities(text)] == ["Eiffel Tower", "Paris", "NASA", "COVID-19", "iPhone", "India", "GDP",
                                               "Reserve Bank of India"]


def test_entity_must_survive_the_round_trip():
    assert check_entities(SRC, HI, BT) == []
    assert check_entities(SRC, HI, BT.replace("Meera Rao", "Mira Rao")) == []  # transliteration drift is fine
    wrong = check_entities(SRC, HI, BT.replace("Meera Rao", "Sita Devi"))
    assert kinds(wrong) == [("term", None, False)] and SRC[slice(*wrong[0].span)] == "Dr Meera Rao"
    assert check_entities("NASA data.", "NASA डेटा।", "The space agency data.") == []  # kept verbatim in the translation
    assert check_entities(SRC, HI, None) == []


def test_run_checks_on_a_clean_item():
    candidates, scores = run_checks(SRC, HI, BT, {"DEVANAGARI"}, reference=HI)
    assert candidates == [] and scores["chrf_pp"] == 100.0


def test_numbers_in_another_number_system_are_the_same_quantity():
    assert check_numbers("Chennai has about 7 million people.", "चेन्नई की आबादी लगभग 70 लाख है।", None) == []
    assert check_numbers("About 4.5 million people live here.", "লগভগ ৪৫ লাখ লোক এখানে থাকে।", None) == []
    assert check_numbers("A budget of 2 billion rupees.", "200 करोड़ रुपये का बजट।", None) == []
    wrong = check_numbers("Chennai has about 7 million people.", "चेन्नई की आबादी लगभग 60 लाख है।", None)
    assert kinds(wrong) == [("number", "changed", True)]


def test_table_cells_and_headings_are_not_names():
    md = "### Caring for a bicycle\n\n| Season | Months |\n|---|---|\n| Summer | March to May |\n\nThe Bay of Bengal is east."
    assert [e for e, _ in entities(md)] == ["May", "Bay of Bengal"]  # "March" starts a cell


def test_names_survive_rewording_and_transliteration():
    src = "Marie Curie was a Polish-born French physicist. Dr Meera Rao agreed."
    back = "Marie Curie was a Polish-French physicist. Dr Mira Rao agreed."
    assert check_entities(src, "…", back) == []
    assert [c.evidence["entity"] for c in check_entities(src, "…", back.replace("Mira Rao", "Sita Devi"))] == ["Dr Meera Rao"]


def test_dollar_amounts_are_not_math():
    # From IN22-Gen: "$3 million ($" looked like inline math, hiding the numbers and faking a format break.
    source = "Exports rose from $3 million ($2 million from bananas) to $25 million."
    translation = "निर्यात 3 मिलियन डॉलर (2 मिलियन डॉलर केलों से) से बढ़कर 25 मिलियन डॉलर हो गया।"
    assert check_format(source, translation) == []
    assert check_numbers(source, translation, back=source) == []  # "डॉलर" matches via the back-translation
    assert check_format("Energy is $E = mc^2$.", "ऊर्जा E = mc^2 है।")  # real math is still checked
