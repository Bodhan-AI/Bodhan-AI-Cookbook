from bodhan_eval.checks.markup import md_blocks, protected_regions
from bodhan_eval.checks.translation import check_format
from bodhan_eval.params import Params

P = Params.defaults()

SRC_TABLE = """## Planets

| Planet | Moons |
|---|---|
| Earth | 1 |
| Mars | 2 |
"""
TR_TABLE = """## ग्रह

| ग्रह | उपग्रह |
|---|---|
| पृथ्वी | 1 |
| मंगल | 2 |
"""


def test_blocks_parse():
    kinds = [b.kind for b in md_blocks(SRC_TABLE)]
    assert kinds == ["h2", "table_row", "table_sep", "table_row", "table_row"]


def test_clean_markdown_table_has_no_flags():
    assert check_format(SRC_TABLE, TR_TABLE, P).flags == []


def test_broken_table_row_is_localized():
    tr = TR_TABLE.replace("| मंगल | 2 |", "| मंगल 2 |")
    res = check_format(SRC_TABLE, tr, P)
    assert [f.category for f in res.flags] == ["format"]
    s, e = res.flags[0].span
    assert tr[s:e] == "| मंगल 2 |"


def test_lost_heading_marker():
    tr = TR_TABLE.replace("## ग्रह", "ग्रह")
    res = check_format(SRC_TABLE, tr, P)
    assert any(f.category == "format" and "heading" in f.evidence for f in res.flags)


def test_missing_table_separator():
    tr = TR_TABLE.replace("|---|---|\n", "")
    res = check_format(SRC_TABLE, tr, P)
    assert any(f.category == "format" and "separator" in f.evidence for f in res.flags)


def test_dropped_list_item_is_omission_candidate():
    src = "Steps:\n\n- Boil water\n- Add tea\n- Serve hot\n"
    tr = "चरण:\n\n- पानी उबालें\n- गरम परोसें\n"
    res = check_format(src, tr, P)
    assert [f.category for f in res.flags] == ["omission"]


def test_latex_ok_broken_dollar_and_changed_command():
    src = "The energy is $E = mc^2$ and the root is $\\sqrt{x}$."
    ok = "ऊर्जा $E = mc^2$ है और मूल $\\sqrt{x}$ है।"
    assert check_format(src, ok, P).flags == []

    broken = "ऊर्जा $E = mc^2 है और मूल $\\sqrt{x}$ है।"
    res = check_format(src, broken, P)
    assert res.flags and all(f.category == "format" for f in res.flags)

    changed = "ऊर्जा $E = mc^2$ है और मूल $\\frac{x}$ है।"
    res = check_format(src, changed, P)
    assert any("LaTeX commands differ" in f.evidence for f in res.flags)


def test_latex_text_contents_may_be_translated():
    src = "$$v = \\frac{d}{t} \\text{ where d is distance}$$"
    tr = "$$v = \\frac{d}{t} \\text{ जहाँ d दूरी है}$$"
    assert check_format(src, tr, P).flags == []


def test_unbalanced_braces():
    src = "Use $\\frac{a}{b}$ here."
    tr = "यहाँ $\\frac{a}{b$ का उपयोग करें।"
    res = check_format(src, tr, P)
    assert any("braces" in f.evidence for f in res.flags)


def test_currency_dollars_are_not_math():
    regions = protected_regions("It costs $5 and $10 today.")
    assert not any(r.kind.startswith("math") for r in regions)


def test_link_dropped():
    src = "Read the [guide](https://x.org/g) first."
    tr = "पहले guide पढ़ें।"
    res = check_format(src, tr, P)
    assert any("link" in f.evidence for f in res.flags)
