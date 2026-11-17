"""Each deterministic and round-trip check, on small hand-built cases."""

from __future__ import annotations

from bodhan_eval.checks.numbers import check_numbers
from bodhan_eval.checks.roundtrip import check_backtranslation, check_latin_terms, source_entities
from bodhan_eval.checks.script import check_script
from bodhan_eval.checks.textual import LengthModel, check_format, check_length
from bodhan_eval.config import Thresholds
from bodhan_eval.models import Category
from bodhan_eval.text import markup
from conftest import EN, HI, HI_NATIVE_DIGITS, make_ctx

TH = Thresholds()


def cats(ctx) -> list[str]:
    return [s.category.value for s in ctx.signals]


# script / language -----------------------------------------------------------------


def test_clean_hindi_has_no_script_signal():
    ctx = make_ctx(EN, HI)
    check_script(ctx, TH)
    assert ctx.signals == []


def test_whole_translation_in_wrong_script_is_critical():
    ctx = make_ctx(EN, "தண்ணீர் கடல் மட்டத்தில் 100 °C இல் கொதிக்கிறது.")
    check_script(ctx, TH)
    sig = ctx.signals[0]
    assert sig.category == Category.WRONG_LANGUAGE and sig.severity.value == "critical" and sig.span == (0, len(ctx.translation))


def test_untranslated_english_run_is_localized():
    tr = "पानी उबलता है। The rest of this sentence was left in English."
    ctx = make_ctx("Water boils. The rest of this sentence was left in English.", tr)
    check_script(ctx, TH)
    sig = next(s for s in ctx.signals if s.category == Category.WRONG_LANGUAGE)
    assert tr[sig.span[0] : sig.span[1]].startswith("The rest")


def test_short_english_entity_is_not_untranslated():
    ctx = make_ctx("NASA launched the James Webb telescope.", "NASA ने James Webb दूरबीन लॉन्च की।")
    check_script(ctx, TH)
    assert Category.WRONG_LANGUAGE not in [s.category for s in ctx.signals]


def test_marathi_in_a_hindi_item_is_caught_by_function_words():
    mr = "तो म्हणाला की आता आमच्याकडे उंदीर आहेत आणि ते मधुमेह मुक्त आहेत. त्यांनी हे काम केले आहे."
    ctx = make_ctx("He said that we now have mice that are free of diabetes.", mr)
    check_script(ctx, TH)
    assert "wrong_language" in cats(ctx)


def test_assamese_letters_in_a_bengali_item():
    ctx = make_ctx("He went home and ate.", "তেওঁ ঘৰলৈ গ'ল আৰু ভাত খালে। ৰাতি বৰষুণ আহিল।", lang="bn")
    check_script(ctx, TH)
    assert "wrong_language" in cats(ctx)


def test_unknown_language_still_runs_generic_checks():
    ctx = make_ctx("Water boils at 100 degrees.", "Hii ni maji 200", lang="sw")
    check_script(ctx, TH)
    check_numbers(ctx)
    assert "number" in cats(ctx)


# numbers ---------------------------------------------------------------------------


def test_native_digits_match_ascii_source():
    ctx = make_ctx(EN, HI_NATIVE_DIGITS)
    check_numbers(ctx)
    assert [s for s in ctx.signals if s.category == Category.NUMBER] == []


def test_changed_number_is_flagged_on_both_sides():
    tr = HI.replace("100", "110")
    ctx = make_ctx(EN, tr)
    check_numbers(ctx)
    sig = ctx.signals[0]
    assert sig.category == Category.NUMBER and sig.score >= 0.9
    assert tr[sig.span[0] : sig.span[1]] == "110" and EN[sig.source_span[0] : sig.source_span[1]] == "100"


def test_number_spelled_out_is_explained_by_back_translation():
    ctx = make_ctx("She bought 3 apples.", "उसने तीन सेब खरीदे।", back_translation="She bought 3 apples.")
    check_numbers(ctx)
    assert ctx.signals == []


def test_missing_number_without_round_trip_is_weak():
    ctx = make_ctx("She bought 3 apples.", "उसने तीन सेब खरीदे।")
    check_numbers(ctx)
    assert ctx.signals and ctx.signals[0].score < 0.5


def test_indian_scale_conversion_is_not_an_error():
    ctx = make_ctx("The city has 3 million people.", "शहर में 30 लाख लोग हैं।")
    check_numbers(ctx)
    assert ctx.signals == []


def test_dropped_percent_symbol():
    ctx = make_ctx("Turnout was 60% this year.", "इस साल मतदान 60 रहा।")
    check_numbers(ctx)
    assert any("%" in s.message for s in ctx.signals)


def test_unit_change_in_round_trip():
    ctx = make_ctx("The road is 5 km long.", "सड़क 5 मील लंबी है।", back_translation="The road is 5 miles long.")
    check_numbers(ctx)
    assert any("unit changed" in s.message for s in ctx.signals)


# format ----------------------------------------------------------------------------


TABLE_SRC = "| # | Item |\n|---|---|\n| 1 | Water |\n| 2 | Salt |"
TABLE_OK = "| # | वस्तु |\n|---|---|\n| 1 | पानी |\n| 2 | नमक |"


def test_clean_table_has_no_format_issue():
    assert markup.compare(TABLE_SRC, TABLE_OK) == []


def test_dropped_table_cell_is_localized_to_its_row():
    broken = "| # | वस्तु |\n|---|---|\n| 1 पानी |\n| 2 | नमक |"
    ctx = make_ctx(TABLE_SRC, broken)
    check_format(ctx)
    sig = ctx.signals[0]
    assert sig.category == Category.FORMAT
    assert "पानी" in broken[sig.span[0] : sig.span[1]]


def test_missing_separator_row():
    broken = "| # | वस्तु |\n| 1 | पानी |\n| 2 | नमक |"
    issues = markup.compare(TABLE_SRC, broken)
    assert any("table sep" in i.message for i in issues)


def test_latex_digit_change_is_a_number_error_and_other_change_is_format():
    src = "Energy: $E = mc^2$."
    assert markup.compare(src, "ऊर्जा: $E = mc^3$.")[0].category == "number"
    assert markup.compare(src, "ऊर्जा: $E = mb^2$.")[0].category == "format"


def test_missing_closing_dollar_is_flagged():
    issues = markup.compare("Use $a + b$ here.", "यहाँ $a + b का प्रयोग करें।")
    assert any("unbalanced $" in i.message or "missing" in i.message for i in issues)


def test_currency_dollar_is_not_math():
    assert markup.math_segments("It costs $5 and $10.") == []
    assert markup.compare("It costs $5 and $10.", "इसकी कीमत $5 और $10 है।") == []


def test_heading_and_list_structure():
    src = "## Title\n\n- one\n- two"
    assert markup.compare(src, "## शीर्षक\n\n- एक\n- दो") == []
    assert markup.compare(src, "शीर्षक\n\n- एक\n- दो")


def test_text_only_items_skip_format_check():
    ctx = make_ctx(EN, HI)
    check_format(ctx)
    assert ctx.checks[0].status == "skipped"


# latin terms -----------------------------------------------------------------------


def test_corrupted_latin_term():
    ctx = make_ctx("The JAS 39C Gripen crashed.", "JAS 39C Gripan दुर्घटनाग्रस्त हो गया।")
    check_latin_terms(ctx)
    sig = ctx.signals[0]
    assert sig.category == Category.TERM and ctx.translation[sig.span[0] : sig.span[1]] == "Gripan"


# length ----------------------------------------------------------------------------


def test_length_outlier_suggests_omission():
    ratios = {"hi": [0.0, 0.02, -0.03, 0.05, 0.01, -0.02, 0.03, 0.0, 0.04, -0.01]}
    model = LengthModel(ratios)
    ctx = make_ctx("A fairly long English sentence that has many words in it, more than a few.", "छोटा।")
    check_length(ctx, model, TH)
    assert "omission" in cats(ctx)


# back-translation ------------------------------------------------------------------


def test_back_translation_omission_points_at_source_clause():
    src = "The committee approved the budget, and the minister resigned after the vote."
    ctx = make_ctx(src, "समिति ने बजट को मंज़ूरी दी।", back_translation="The committee approved the budget.")
    check_backtranslation(ctx, TH)
    sig = next(s for s in ctx.signals if s.category == Category.OMISSION)
    assert "minister" in src[sig.source_span[0] : sig.source_span[1]]


def test_noisy_back_translation_lowers_scores():
    src = "The committee approved the budget, and the minister resigned after the vote."
    good = make_ctx(src, "x", back_translation="The committee approved the budget.")
    noisy = make_ctx(src, "x", back_translation="A group said yes to money.")
    check_backtranslation(good, TH)
    check_backtranslation(noisy, TH)
    best_good = max(s.score for s in good.signals if s.category == Category.OMISSION)
    best_noisy = max((s.score for s in noisy.signals if s.category == Category.OMISSION), default=0)
    assert best_noisy < best_good


def test_entity_lost_in_round_trip():
    ctx = make_ctx("Marie Curie won the prize in Stockholm.", "x", back_translation="Mary Kerry won the prize in Stockholm.")
    check_backtranslation(ctx, TH)
    assert any(s.category == Category.TERM and "Marie Curie" in s.message for s in ctx.signals)


def test_source_entities_skip_sentence_initial_words():
    ents = [e.text for e in source_entities("The doctor met Marie Curie. NASA agreed.")]
    assert "The" not in ents and "Marie Curie" in ents and "NASA" in ents
