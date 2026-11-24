from bodhan_eval.checks import (
    check_against_reference,
    check_dates_times,
    check_entities,
    check_latex,
    check_markdown_structure,
    check_numbers,
    check_reference,
    check_terminology,
    check_units,
    extract_quantities,
    run_deterministic_checks,
)
from bodhan_eval.metrics import chrf

# -- numbers --------------------------------------------------------------------

def test_number_order_does_not_matter():
    result = check_numbers("The team completed 8 out of 10 tasks.", "10 में से 8 काम पूरे हुए।", "hi")
    assert result["status"] == "pass"


def test_indian_scale_words_match_million():
    assert check_numbers("more than 1 million records", "10 लाख से अधिक रिकॉर्ड", "hi")["status"] == "pass"
    assert check_numbers("approximately 2 million", "প্রায় ২০ লক্ষ", "bn")["status"] == "pass"
    assert check_numbers("50 lakh", "5 मिलियन", "hi")["status"] == "pass"


def test_thousands_separators_and_native_digits():
    assert extract_quantities("১০,০০০ জন") == ["10000"]
    assert extract_quantities("1,00,000 और 1,250") == ["100000", "1250"]


def test_spelled_out_number_is_review_not_fail():
    result = check_numbers("The meeting starts at 10 AM.", "कल सुबह दस बजे मीटिंग शुरू होगी।", "hi")
    assert result["status"] == "review"
    assert result["passed"] is None


def test_changed_number_still_fails():
    result = check_numbers("The bus leaves every 30 minutes.", "बस हर 40 मिनट में निकलती है।", "hi")
    assert result["status"] == "fail"
    assert result["evidence"]["missing_in_translation"] == ["30"]
    assert result["evidence"]["extra_in_translation"] == ["40"]


# -- dates, times, units ----------------------------------------------------------

def test_am_pm_swap_fails():
    result = check_dates_times("The train will arrive at 6 PM.", "ट्रेन सुबह 6 बजे पहुंचेगी।", "hi")
    assert result["status"] == "fail"


def test_month_change_fails_and_correct_month_passes():
    assert check_dates_times("Due December 15, 2026.", "15 नवंबर, 2026 तक।", "hi")["status"] == "fail"
    assert check_dates_times("Due December 15, 2026.", "15 ডিসেম্বর, 2026", "bn")["status"] == "pass"


def test_clock_time_must_survive():
    assert check_dates_times("Opens at 9:30 AM.", "सुबह 9:30 बजे खुलता है।", "hi")["status"] == "pass"
    assert check_dates_times("Opens at 9:30 AM.", "सुबह 9 बजे खुलता है।", "hi")["status"] == "fail"


def test_modal_may_is_not_a_month():
    assert check_dates_times("It may rain.", "बारिश हो सकती है।", "hi") is None


def test_unit_swap_fails_missing_unit_reviews():
    assert check_units("2 kilometers away", "2 मीटर दूर", "hi")["status"] == "fail"
    assert check_units("2 kilometers away", "2 किलोमीटर दूर", "hi")["status"] == "pass"
    assert check_units("must not exceed 20 MB", "20 জিবি", "bn")["status"] == "fail"
    assert check_units("2 kilometers away", "2 दूर", "hi")["status"] == "review"


def test_units_without_quantity_are_ignored():
    assert check_units("Wait a second, please.", "कृपया रुकिए।", "hi") is None


# -- entities and terminology -----------------------------------------------------

def test_email_must_be_verbatim():
    assert check_entities("Mail help@example.com.", "help@example.com पर", "hi")["status"] == "pass"
    assert check_entities("Mail help@example.com.", "help@exmple.com पर", "hi")["status"] == "fail"


def test_latin_entity_substitution_fails_transliteration_reviews():
    src = "The application supports Android and iOS."
    assert check_entities(src, "यह Android और iOS को सपोर्ट करता है।", "hi")["status"] == "pass"
    assert check_entities(src, "यह Windows और iOS को सपोर्ट करता है।", "hi")["status"] == "fail"
    assert check_entities(src, "यह एंड्रॉइड और iOS को सपोर्ट करता है।", "hi")["status"] == "review"


def test_place_change_fails():
    src = "The event will take place in New Delhi."
    assert check_entities(src, "कार्यक्रम नई दिल्ली में होगा।", "hi")["status"] == "pass"
    assert check_entities(src, "कार्यक्रम मुंबई में होगा।", "hi")["status"] == "fail"


def test_glossary_terminology():
    glossary = {"dashboard": ["डैशबोर्ड"]}
    assert check_terminology("Open the dashboard.", "डैशबोर्ड खोलें।", glossary)["status"] == "pass"
    assert check_terminology("Open the dashboard.", "नियंत्रण पट खोलें।", glossary)["status"] == "fail"
    assert check_terminology("Open the file.", "फ़ाइल खोलें।", glossary) is None


# -- Markdown and LaTeX -------------------------------------------------------------

MD_SOURCE = "## Setup\n\nRun `pip install x` and read [docs](https://a.b/c).\n\n- one\n- two"
MD_GOOD = "## सेटअप\n\n`pip install x` चलाएँ और [दस्तावेज़](https://a.b/c) पढ़ें।\n\n- एक\n- दो"


def test_markdown_structure_preserved():
    assert check_markdown_structure(MD_SOURCE, MD_GOOD)["status"] == "pass"


def test_markdown_broken_link_and_code_fail_high():
    broken = MD_GOOD.replace("[दस्तावेज़](https://a.b/c)", "दस्तावेज़").replace("`pip install x`", "`पिप install x`")
    result = check_markdown_structure(MD_SOURCE, broken)
    assert result["status"] == "fail" and result["severity"] == "high"
    assert {"link_urls", "inline_code"} <= set(result["evidence"]["mismatches"])


def test_markdown_lost_list_and_heading():
    flattened = "सेटअप `pip install x` [दस्तावेज़](https://a.b/c) एक दो"
    mismatches = check_markdown_structure(MD_SOURCE, flattened)["evidence"]["mismatches"]
    assert "headings" in mismatches and "bullets" in mismatches


def test_latex_must_be_verbatim():
    src = r"Energy is $E = mc^2$ and \(\frac{a}{b}\)."
    assert check_latex(src, r"ऊर्जा $E = mc^2$ और \(\frac{a}{b}\) है।")["status"] == "pass"
    bad = check_latex(src, r"ऊर्जा $E = mc^3$ और \(\frac{a}{b\) है।")
    assert bad["status"] == "fail"
    assert bad["evidence"]["unbalanced_segments"]


def test_run_checks_adds_optional_checks_only_when_relevant():
    plain = run_deterministic_checks({"source_text": "Hello.", "translation": "नमस्ते।", "language": "hi"})
    assert [c["category"] for c in plain] == ["number_mismatch", "formatting_mismatch"]

    md = run_deterministic_checks({"source_text": MD_SOURCE, "translation": MD_GOOD, "language": "hi"})
    assert "markdown_structure" in {c["category"] for c in md}


# -- references -----------------------------------------------------------------------

def test_chrf_bounds():
    assert chrf("abc", "abc") == 1.0
    assert chrf("", "abc") == 0.0
    assert 0 < chrf("कीमत 500 रुपये", "कीमत 500 रुपये है") < 1


def test_reference_check_only_with_independent_reference():
    record = {"source_text": "Hi", "translation": "नमस्ते", "language": "hi", "reference_translation": None}
    assert "reference_chrf" not in {c["category"] for c in run_deterministic_checks(record)}

    record["reference_translation"] = "नमस्ते"
    check = next(c for c in run_deterministic_checks(record) if c["category"] == "reference_chrf")
    assert check["status"] == "pass"
    assert "identical" in check["evidence"]["note"]


def test_low_reference_similarity_is_review():
    result = check_reference("बिल्कुल अलग वाक्य यहाँ", "कुछ और लिखा है")
    assert result["status"] == "review"


def test_reference_diff_reports_each_replacement_once():
    findings = check_against_reference("बैठक सुबह शुरू होगी", "बैठक शाम शुरू होगी")
    assert len(findings) == 1
    assert findings[0]["category"] == "reference_mismatch"


def test_reference_diff_classifies_units_and_entities():
    unit = check_against_reference("2 किलोमीटर दूर", "2 मीटर दूर", "hi")
    assert unit[0]["category"] == "unit_mismatch"
    entity = check_against_reference("Android पर चलता है", "Windows पर चलता है", "hi")
    assert entity[0]["category"] == "entity_mismatch"


def test_reference_diff_handles_suffixed_numbers_and_latin_units():
    # Bengali counter suffix: a number swap, already covered by check_numbers.
    assert check_against_reference("৭টি কাজ", "999টি কাজ", "bn") == []
    # tokenize() lower-cases "MB"/"GB"; still a unit swap, not an entity swap.
    swap = check_against_reference("20 MB வரை", "20 GB வரை", "ta")
    assert [f["category"] for f in swap] == ["unit_mismatch"]
