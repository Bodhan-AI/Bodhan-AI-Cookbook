"""Markdown and LaTeX structure extraction (TESTING.md §1)."""

from __future__ import annotations

import pytest

from bodhan_eval.markup import (
    brace_problems,
    has_markup,
    in_spans,
    math_key,
    math_segments,
    md_lines,
    protected_spans,
    table_cols,
    unbalanced_dollars,
    unmatched_marker_positions,
)


@pytest.mark.parametrize(
    ("text", "kind", "body"),
    [
        pytest.param("so $x^2$ here", "d", "x^2", id="inline-dollar"),
        pytest.param("so $$E=mc^2$$ here", "dd", "E=mc^2", id="display-dollar"),
        pytest.param(r"so \(a+b\) here", "pa", "a+b", id="paren"),
        pytest.param(r"so \[a+b\] here", "br", "a+b", id="bracket"),
        pytest.param(r"\begin{align}a&=b\end{align}", "env", "align:a&=b", id="environment"),
    ],
)
def test_math_segment_kinds(text: str, kind: str, body: str) -> None:
    segs = math_segments(text)
    assert [(s.kind, s.body) for s in segs] == [(kind, body)]
    assert text[segs[0].start : segs[0].end].endswith(("$", ")", "]", "}"))


def test_escaped_dollars_are_not_math() -> None:
    text = r"It costs \$5 or \$6."
    assert math_segments(text) == []
    assert unbalanced_dollars(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("$x$ and $y$", [], id="balanced"),
        pytest.param("area $a^2 is big", [5], id="stray-open"),
        pytest.param("$x$ then $", [9], id="stray-after-math"),
    ],
)
def test_unbalanced_dollars(text: str, expected: list[int]) -> None:
    assert unbalanced_dollars(text) == expected


@pytest.mark.parametrize(
    ("body", "broken"),
    [
        pytest.param(r"\frac{a}{b}", False, id="balanced"),
        pytest.param(r"\frac{a}{b", True, id="missing-close"),
        pytest.param("a}", True, id="extra-close"),
        pytest.param(r"\{a", False, id="escaped-brace"),
    ],
)
def test_brace_problems(body: str, broken: bool) -> None:
    assert brace_problems(body) is broken


def test_math_key_treats_braces_around_one_token_as_the_same_formula() -> None:
    assert math_key("ax^2 + bx + c = 0") == math_key("ax^{2} + bx + c = 0")  # seen live: Bengali added the braces
    assert math_key(r"x_{i} + y^{\alpha}") == math_key(r"x_i + y^\alpha")
    assert math_key("x^{10}") != math_key("x^10")  # ^10 is x to the power 1, followed by 0
    assert math_key("x^{2}") != math_key("x^{3}")


def test_math_key_ignores_text_contents_and_whitespace() -> None:
    assert math_key(r"\text{speed} = d / t") == math_key(r"\text{गति}=d/t")
    assert math_key("v = d/t") != math_key("v = d/s")


@pytest.mark.parametrize(
    ("line", "kind", "cols"),
    [
        pytest.param("| a | b | c |", "table", 3, id="outer-pipes"),
        pytest.param("a | b | c", "table", 3, id="no-outer-pipes"),
        pytest.param("|---|:---:|---:|", "table_sep", 3, id="separator-outer-pipes"),
        pytest.param("---|---", "table_sep", 2, id="separator-no-outer-pipes"),
        pytest.param("## Heading", "heading2", 0, id="heading"),
        pytest.param("- item", "ul", 0, id="bullet"),
        pytest.param("2. item", "ol", 0, id="numbered"),
        pytest.param("> quote", "quote", 0, id="quote"),
        pytest.param("```python", "fence", 0, id="fence"),
        pytest.param("---", "text", 0, id="rule-is-not-a-table"),
        pytest.param("Plain prose.", "text", 0, id="prose"),
    ],
)
def test_md_line_kinds(line: str, kind: str, cols: int) -> None:
    (ln,) = md_lines(line)
    assert (ln.kind, ln.cols) == (kind, cols)


def test_md_lines_offsets_point_into_text() -> None:
    text = "# T\n\n| a | b |\nend"
    lines = md_lines(text)
    assert [ln.kind for ln in lines] == ["heading1", "blank", "table", "text"]
    assert [text[ln.start : ln.end] for ln in lines] == ["# T", "", "| a | b |", "end"]


def test_table_cols_keeps_escaped_pipe_inside_cell() -> None:
    assert table_cols(r"| a \| b | c |") == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("Water boils at 100 °C at sea level.", False, id="plain-prose"),
        pytest.param("पानी 100 डिग्री पर उबलता है।", False, id="plain-indic"),
        pytest.param("Use $v=d/t$.", True, id="math"),
        pytest.param("Run `ls` now", True, id="inline-code"),
        pytest.param("very **bold**", True, id="bold"),
        pytest.param("| a | b |\n|---|---|", True, id="table"),
        pytest.param("See [docs](https://example.org/a).", True, id="markdown-link"),
    ],
)
def test_has_markup(text: str, expected: bool) -> None:
    assert has_markup(text) is expected


def test_protected_spans_cover_math_code_and_urls() -> None:
    text = "see $x$ and `y` at https://example.org/a now"
    spans = protected_spans(text)
    covered = {text[a:b] for a, b in spans}
    assert covered == {"$x$", "`y`", "https://example.org/a"}
    assert in_spans(text.index("x"), spans)
    assert not in_spans(text.index("now"), spans)


def test_unmatched_markers() -> None:
    assert unmatched_marker_positions("**bold** and **broken", "**") == [13]
    assert unmatched_marker_positions("**ok** fine", "**") == []
    assert unmatched_marker_positions("`a` and `b", "`") == [8]
