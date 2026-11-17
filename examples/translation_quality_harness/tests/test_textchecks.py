from bodhan_eval.textchecks import (
    check_backtranslation,
    check_entities,
    check_format,
    check_numbers,
    check_script,
    clauses,
    entities,
    length_outliers,
)

SRC = "Water boils at 100 °C at sea level."
HI = "पानी समुद्र तल पर 100 °C पर उबलता है।"


def test_matching_numbers_pass_even_in_native_digits():
    assert check_numbers(SRC, HI) == []
    assert check_numbers(SRC, HI.replace("100", "१००")) == []


def test_changed_number_is_flagged_and_localized():
    bad = HI.replace("100", "108")
    [c] = check_numbers(SRC, bad)
    assert c.category == "number" and c.score >= 0.9
    assert bad[slice(*c.span)] == "108"


def test_missing_number_points_into_source():
    [c] = check_numbers(SRC, "पानी समुद्र तल पर उबलता है।")
    assert c.span is None and SRC[slice(*c.source_span)] == "100"


def test_spelled_out_source_numbers_are_not_false_alarms():
    assert check_numbers("Laws of motion: three of them.", "गति के 3 नियम हैं।") == []


def test_table_with_missing_cell_is_flagged():
    src = "| a | b |\n|---|---|\n| 1 | 2 |"
    tgt = "| क | ख |\n|---|---|\n| 1  2 |"
    flags = check_format(src, tgt)
    assert any(f.evidence["problem"].startswith("table") for f in flags)
    span = next(f.span for f in flags if f.evidence["problem"].startswith("table"))
    assert tgt[slice(*span)] == "| 1  2 |"


def test_latex_and_bold_must_survive():
    src = "Area is $A = \\pi r^2$ and **bold**."
    assert check_format(src, "क्षेत्र $A = \\pi r^2$ और **मोटा** है।") == []
    problems = {f.evidence["problem"].split(" (")[0] for f in check_format(src, "क्षेत्र $A = \\pi r^2 और **मोटा है।")}
    assert "LaTeX $ delimiter count differs" in problems and "** bold marker count differs" in problems


def test_merged_bold_is_minor_but_unbalanced_bold_is_major():
    # Seen live (md-003.gu): Gujarati word order merged "**120 km** in **2 hours**" into one bold span.
    src = "A bus covers **120 km** in **2 hours**."
    [merged] = check_format(src, "બસ **2 કલાકમાં 120 કિમી** કાપે છે.")
    assert merged.severity == "minor" and merged.score < 0.6
    [broken] = check_format(src, "બસ **2 કલાકમાં 120 કિમી કાપે છે.")
    assert broken.severity == "major" and broken.score == 1.0


def test_untranslated_run_is_flagged_but_names_and_math_are_not():
    src = "Newton wrote the laws of motion in 1687."
    assert check_script(src, "Newton ने $F = ma$ लिखा।", "hi") == []
    [c] = check_script(src, "न्यूटन ने laws of motion लिखा।", "hi")
    assert c.category == "wrong_language" and "laws of motion" in c.evidence["untranslated_text"]


def test_whole_text_in_wrong_script():
    [c] = check_script(SRC, "தண்ணீர் 100 °C இல் கொதிக்கிறது", "hi")
    assert c.score >= 0.9 and c.evidence["found_script"] == "TAMIL"


def test_backtranslation_flags_a_dropped_clause_not_paraphrase():
    src = "Plants absorb carbon dioxide during the day and release oxygen into the air at night."
    assert check_backtranslation(src, "During the day, plants take in carbon dioxide and release oxygen into the air at night.") == []
    src2 = "Wash your hands before eating. Always keep the drinking water container covered with a clean lid."
    [c] = check_backtranslation(src2, "Wash your hands before eating.")
    assert c.category == "omission" and "container" in src2[slice(*c.source_span)]


def test_entities_skip_sentence_initial_words_but_keep_names_and_acronyms():
    found = [w for w, _, _ in entities("The Reserve Bank of India was set up in Mumbai. NASA uses H2O and COVID-19 data.")]
    assert "Mumbai" in found and "NASA" in found and "H2O" in found and "COVID-19" in found
    assert "The" not in found


def test_entity_missing_from_backtranslation_is_a_weak_term_candidate():
    src = "Isaac Newton described three laws of motion."
    assert check_entities(src, "Isaac Newton described the three laws of motion.") == []
    [c] = check_entities(src, "Albert Einstein described the three laws of motion.")
    assert c.category == "term" and c.score < 0.6 and src[slice(*c.source_span)] == "Isaac Newton"
    assert check_entities(src, "आइज़ैक न्यूटन") == []  # non-English back-translation: checker failure, not evidence


def test_clauses_split_on_sentences():
    assert len(clauses("One two three. Four five six; seven eight nine")) == 3


def test_length_outliers_use_batch_median():
    rows = [(f"i{k}", "x" * 50, "y" * 60) for k in range(10)] + [("short", "x" * 50, "y" * 10)]
    out = length_outliers(rows)
    assert list(out) == ["short"] and out["short"][0].category == "omission"
