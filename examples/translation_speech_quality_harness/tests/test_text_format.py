"""Format check compares source structure with translation structure (no reference needed)."""

from bodhan_eval.checks import LangStats, check_format
from bodhan_eval.models import Item

STATS = LangStats()

SOURCE = (
    "## Results\n\n"
    "| name | value |\n|---|---|\n| a | 1 |\n\n"
    "- first item\n- second item\n\n"
    "See [the docs](https://example.com/docs) and use {user_name}.\n\n"
    "We have $x^2 + y^2 = z^2$ and $$E = mc^2$$ here.\n\n"
    "```\ncode()\n```\n"
)
# Same structure, prose replaced by non-Latin placeholder text (U+0915 repeated).
GOOD = (
    "## \u0915\u0915\u0915\n\n"
    "| \u0915 | \u0916 |\n|---|---|\n| \u0915 | 1 |\n\n"
    "- \u0915\u0915\n- \u0916\u0916\n\n"
    "\u0915\u0915 [\u0915\u0915](https://example.com/docs) \u0915\u0915 {user_name}.\n\n"
    "\u0915\u0915 $x^2 + y^2 = z^2$ \u0915 $$E = mc^2$$ \u0915\u0964\n\n"
    "```\ncode()\n```\n"
)


def _item(translation: str, source: str = SOURCE, reference: str | None = None) -> Item:
    return Item(id="f", lang="hi", source=source, translation=translation, reference=reference)


def test_clean_translation_has_no_format_flag_without_reference():
    assert check_format(_item(GOOD), STATS) == []


def test_removed_table_pipes_flagged_without_reference():
    broken = GOOD.replace("|", "")
    flags = check_format(_item(broken), STATS)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.category == "format"
    assert flag.span is not None and 0 <= flag.span[0] <= flag.span[1] <= len(broken)
    assert flag.source_span is not None and flag.source_span[1] <= len(SOURCE)
    assert any(key.startswith("tbl") for key in flag.evidence["missing"])


def test_stripped_math_delimiters_flagged():
    broken = GOOD.replace("$$E = mc^2$$", "E = mc^2")
    flags = check_format(_item(broken), STATS)
    assert flags and "math$$" in flags[0].evidence["missing"]


def test_latex_command_dropped_flagged():
    source = "The ratio is $\\frac{a}{b}$ overall."
    flags = check_format(_item("\u0915\u0915 $a/b$ \u0915\u0915", source), STATS)
    assert flags and "cmd:\\frac" in flags[0].evidence["missing"]


def test_unbalanced_code_fence_flagged():
    broken = GOOD.replace("```\ncode()\n```\n", "```\ncode()\n")
    assert check_format(_item(broken), STATS)


def test_placeholder_and_url_must_survive_verbatim():
    assert check_format(_item(GOOD.replace("{user_name}", "")), STATS)
    assert check_format(_item(GOOD.replace("https://example.com/docs", "https://example.com/doc")), STATS)


def test_currency_dollar_signs_are_not_math():
    source = "It costs $5 and $10 today."
    assert check_format(_item("\u0915\u0915 \u20b95 \u0915 \u20b910 \u0915\u0915", source), STATS) == []


def test_plain_text_without_markup_is_clean():
    assert check_format(_item("\u0915\u0915\u0915\u0964", "Plain sentence."), STATS) == []


def test_unrelated_reference_is_ignored():
    # The source is the structural ground truth; a reference must not change the verdict.
    assert check_format(_item(GOOD, reference="plain reference"), STATS) == []


def test_added_emphasis_or_table_on_plain_source_is_style_not_breakage():
    source = "A plain notice."
    assert check_format(_item("**\u0915\u0915**", source), STATS) == []
    assert check_format(_item("| \u0915 | \u0916 |\n|---|---|", source), STATS) == []


def test_invented_placeholder_is_flagged():
    flags = check_format(_item("\u0915\u0915 {user_name}", "A plain notice."), STATS)
    assert flags and flags[0].evidence["extra"] == ["ph:{user_name}"]
