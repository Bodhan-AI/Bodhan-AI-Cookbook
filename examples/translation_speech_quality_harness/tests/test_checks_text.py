"""Deterministic checks. Error cases are made by editing real Indic-Translate outputs with string operations."""

import pytest
from conftest import fixture_rows, item

from bodhan_eval.checks_text import check_format, check_numbers, check_script, length_candidates, numbers_in, reference_scores, robust_z, word_numbers

ROWS = fixture_rows()
CLEAN = [r for r in ROWS.values() if r.get("translation")]


def row_item(rid, translation=None):
    r = ROWS[rid]
    return item(r["source"], translation if translation is not None else r["translation"], r["lang"])


def exact(cands):
    return [c for c in cands if c["class"] == "exact"]


@pytest.mark.parametrize("check", [check_numbers, check_format, check_script])
@pytest.mark.parametrize("rid", [r["id"] for r in CLEAN])
def test_real_clean_outputs_raise_nothing_exact(check, rid):
    # Includes native digits (Hindi and Bengali 72), 1,000,000 -> 10 lakh, a Markdown table with LaTeX.
    assert exact(check(row_item(rid))) == []


# ---------------------------------------------------------------- numbers

def test_changed_digit_is_exact_with_both_spans():
    r = ROWS["t01"]
    c = exact(check_numbers(row_item("t01", r["translation"].replace("100", "1000"))))
    assert len(c) == 1 and c[0]["category"] == "number"
    s, e = c[0]["source_span"]
    assert r["source"][s:e] == "100"


def test_changed_native_digit_bengali():
    tr = ROWS["t04"]["translation"].replace("৭২", "৭৩")  # 72 -> 73 in Bengali digits
    c = exact(check_numbers(row_item("t04", tr)))
    assert len(c) == 1 and c[0]["evidence"]["source"] == "72"


def test_missing_number_is_only_noisy():
    c = check_numbers(row_item("t01", ROWS["t01"]["translation"].replace("100 ", "")))
    assert [x["class"] for x in c] == ["noisy"]


def test_unit_change_is_exact():
    r = ROWS["t09"]  # Tamil, keeps "100°C"
    c = exact(check_numbers(item(r["source"], r["translation"].replace("°C", "°F"), "ta")))
    assert len(c) == 1 and c[0]["evidence"]["detail"] == "unit changed"


def test_number_words_and_scale_conversions_are_accepted():
    assert check_numbers(item("I have three apples.", "x 3 y")) == []
    assert check_numbers(item("One million people.", "10 x")) == []
    assert check_numbers(item("1,000,000 people and 2 crore rupees.", "10 lakh x 20000000 y")) == []


def test_number_parsing():
    vals = [n["value"] for n in numbers_in("₹2,450.50, 1,00,000, 3,5 and 1.2.3 then १२")]
    assert [str(v) for v in vals] == ["2450.5", "1E+5", "3.5", "1.2.3", "12"]
    assert numbers_in("$x^2 + 3$ costs ₹5")[0]["currency"] == "₹"  # number inside LaTeX is ignored
    assert [int(v) for v in word_numbers("twenty-five and one hundred and five, then three")] == [25, 105, 3]


# ---------------------------------------------------------------- format

def md(translation_edit):
    r = ROWS["t07"]
    return check_format(item(r["source"], translation_edit(r["translation"])))


def test_dropped_separator_row():
    c = md(lambda t: "\n".join(line for line in t.split("\n") if "---" not in line))
    assert c and c[0]["evidence"]["detail"] == "table structure differs"


def test_dropped_cell():
    def edit(t):
        return "\n".join(line.replace(" | 7.87", " 7.87") if "7.87" in line else line for line in t.split("\n"))
    c = md(edit)
    assert c and c[0]["severity_hint"] == "major"
    assert "7.87" in ROWS["t07"]["translation"][c[0]["translation_span"][0]:c[0]["translation_span"][1]]


@pytest.mark.parametrize("edit", [lambda t: t.replace("mc^2$", "mc^2"), lambda t: t.replace("mc^2", "mc^3")])
def test_latex_broken_or_changed(edit):
    c = md(edit)
    assert any(x["evidence"]["detail"] == "math content differs" for x in c)


def test_heading_level_change():
    c = md(lambda t: t.replace("## ", "### "))
    assert c[0]["evidence"]["detail"] == "heading structure differs" and c[0]["severity_hint"] == "minor"


def test_plain_text_and_bold():
    assert check_format(item("No markdown here.", "nothing here either")) == []
    c = check_format(item("**Bold** text.", "**x y"))
    assert c[0]["evidence"]["detail"] == "unbalanced ** emphasis marker"


def test_text_inside_latex_text_command_may_be_translated():
    assert check_format(item(r"Use $\text{speed} = d/t$.", r"x $\text{y z} = d/t$.")) == []


# ---------------------------------------------------------------- script

def test_tamil_replaced_by_hindi_is_wrong_language():
    c = exact(check_script(item(ROWS["t09"]["source"], ROWS["t09"]["translation"] + " " + ROWS["t01"]["translation"], "ta")))
    assert c and c[0]["evidence"]["detail"].startswith("text is largely in another script")


def test_two_foreign_words_inside_long_text():
    tr = ROWS["t03"]["translation"] + " " + " ".join(ROWS["t01"]["translation"].split()[:2]) + " " + ROWS["t03"]["translation"]
    c = exact(check_script(item(ROWS["t03"]["source"], tr, "ta")))
    assert len(c) == 1 and c[0]["evidence"]["script"] == "DEVANAGARI"


def test_sentence_left_in_english():
    r = ROWS["t02"]
    sentences = r["translation"].split("।")
    tr = "।".join(sentences[:2]) + "। An adult heart beats about 72 times per minute."
    c = exact(check_script(row_item("t02", tr)))
    assert len(c) == 1 and "adult heart" in tr[c[0]["translation_span"][0]:c[0]["translation_span"][1]]


def test_acronyms_names_and_code_are_not_untranslated():
    base = ROWS["t01"]["translation"]
    assert check_script(item("NASA and ISRO say water boils.", base + " NASA ISRO")) == []
    assert check_script(item("Run `pip install numpy now` first.", base + " `pip install numpy now`")) == []


def test_whole_copy_of_source():
    r = ROWS["t02"]
    assert exact(check_script(item(r["source"], r["source"])))


# ---------------------------------------------------------------- run-level statistics

def test_robust_z():
    z = robust_z([1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 10.0])
    assert z[-1] > 3 and all(abs(x) < 3 for x in z[:-1])
    assert robust_z([2.0, 2.0, 2.0]) == [0.0, 0.0, 0.0]


def test_length_outlier_needs_enough_items():
    r = ROWS["t02"]
    items = [item(r["source"], r["translation"]) for _ in range(8)] + [item(r["source"], r["translation"][:20])]
    c, langs = length_candidates(items)
    assert langs == {"hi"} and list(c) == [8] and c[8][0]["category"] == "omission" and c[8][0]["class"] == "weak"
    assert length_candidates(items[:5]) == ({}, set())


def test_reference_scores():
    r = ROWS["t01"]
    scores, cands = reference_scores([{**item(r["source"], r["translation"]), "reference": r["translation"]}])
    assert scores == {0: 100.0} and cands == {}


def test_scale_word_after_digits_in_indic_text():
    # Real Indic-Translate output: 910 million as 91 crore (Tamil), 6.5 million as 65 lakh (Bengali).
    assert check_numbers(item("about 910 million people", "சுமார் 91 கோடி மக்கள்", "ta")) == []
    assert check_numbers(item("about 6.5 million passengers", "প্রায় ৬৫ লক্ষ যাত্রী", "bn")) == []
    c = exact(check_numbers(item("about 6.5 million passengers", "প্রায় ৬৬ লক্ষ যাত্রী", "bn")))
    assert len(c) == 1 and c[0]["evidence"]["detail"] == "number changed"


def test_bare_degree_matches_spelled_out_celsius():
    assert check_numbers(item("Store below 25 °C.", "২৫° সেলসিয়াসের নিচে", "bn")) == []
