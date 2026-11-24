import pytest

from bodhan_lesson.stages.assemble import assemble
from bodhan_lesson.stages.assemble import math_markdown as mm

# Equation blocks exactly as Indic-OCR returned them (page 8 of the demo chapter).
OCR_INLINE_WITH_LABEL = r"$\angle \text{TOS} + \angle \text{SOR} + \angle \text{QOR} = 180^\circ$ (3)"
OCR_PROSE_PREFIX = r"Therefore, $\angle TOS + \angle SOQ = 180^{\circ}$ (2)"
OCR_MULTI_LINE_INLINE = "Now, $\\angle \\text{ROT} = \\angle \\text{ROS}$\n$= \\frac{x}{2} + 90^\\circ$\n$= 90^\\circ$"


# ---- scanning ------------------------------------------------------------------
def test_scan_finds_inline_and_display_spans():
    spans, problems = mm.scan_math(r"a $x$ b $$y = 1$$ c")
    assert [(s.delimiter, s.body) for s in spans] == [("$", "x"), ("$$", "y = 1")] and not problems


def test_escaped_dollar_is_not_math():
    assert mm.scan_math(r"costs \$5 and \$6") == ([], [])
    assert not mm.has_delimiters(r"costs \$5")


def test_unterminated_spans_are_reported():
    assert "unterminated_inline_math" in mm.math_problems("price $5 only")
    assert "unterminated_display_math" in mm.math_problems("$$ x = 1")


@pytest.mark.parametrize("text, expected", [
    (r"$\frac{1}{2$", "unbalanced_braces"),
    (r"$\left( x $", "unbalanced_left_right"),
    (r"$\begin{aligned} a $", "unbalanced_environment"),
    ("$ $", "empty_math"),
    ("$a\n\nb$", "blank_line_in_inline_math"),
    ("$$\n$x$\n$$", "nested_dollar"),
])
def test_span_problems(text, expected):
    assert expected in mm.math_problems(text)


def test_valid_math_has_no_problems():
    assert mm.math_problems(r"$\left( \frac{x}{2} \right)$ and $$\begin{aligned} a &= b \end{aligned}$$ and \{ \}") == []


# ---- shaping -------------------------------------------------------------------
def test_bare_latex_is_wrapped_as_display():
    assert mm.shape_equation(r"\angle ABC = 90^\circ") == "$$\n\\angle ABC = 90^\\circ\n$$"


def test_existing_display_span_is_kept():
    text = r"$$ \angle ABC = \angle ABD $$"
    assert mm.shape_equation(text) == text and mm.is_single_display_span(text)


@pytest.mark.parametrize("ocr_text", [OCR_INLINE_WITH_LABEL, OCR_PROSE_PREFIX, OCR_MULTI_LINE_INLINE])
def test_inline_spans_are_never_wrapped_in_display_math(ocr_text):
    shaped = mm.shape_equation(ocr_text)
    assert shaped == ocr_text and not shaped.startswith("$$")
    assert mm.math_problems(shaped) == []


def test_repair_removes_dollars_nested_in_display_math():
    repaired = mm.repair_nested_dollars("$$\n$x + y$\n$$")
    assert repaired == "$$\nx + y\n$$" and mm.math_problems(repaired) == []


def test_repair_leaves_clean_text_alone():
    text = r"keep $a$ and $$b$$ and \$ here"
    assert mm.repair_nested_dollars(text) == text


# ---- through the assembler -------------------------------------------------------
@pytest.mark.parametrize("ocr_text", [OCR_INLINE_WITH_LABEL, OCR_PROSE_PREFIX, OCR_MULTI_LINE_INLINE])
def test_assembled_equation_blocks_parse(make_block, make_page, ocr_text):
    lesson = assemble([make_page(1, [make_block(ocr_text, type="Equation", order=0)])])
    element = lesson.elements[0]
    assert mm.math_problems(lesson.markdown) == [] and not element.doubtful and element.math_issues == []


def test_nested_dollars_in_a_paragraph_are_repaired_and_recorded(make_block, make_page):
    lesson = assemble([make_page(1, [make_block("So $$\n$x = 1$\n$$ holds.", order=0)])])
    element = lesson.elements[0]
    assert mm.math_problems(element.markdown) == [] and element.math_issues == ["repaired:nested_dollar"]
    assert not element.doubtful


def test_unrecoverable_math_is_fenced_and_flagged_not_dropped(make_block, make_page):
    lesson = assemble([make_page(1, [make_block(r"Bad $\frac{1}{2$ span here.", order=0)])])
    element = lesson.elements[0]
    assert element.doubtful and "unbalanced_braces" in element.math_issues
    assert "```text" in element.markdown and r"Bad $\frac{1}{2$ span here." in element.markdown
    assert "<!-- doubtful p001.b00: math_unparsable:unbalanced_braces -->" in element.markdown


def test_unrecoverable_math_in_a_heading_is_flagged_without_a_fence(make_block, make_page):
    heading = make_block(r"6.1 The $\frac{1}{2$ rule", type="SectionHeader", label="Section-title", order=0)
    element = assemble([make_page(1, [heading])]).elements[0]
    assert element.doubtful and "```" not in element.markdown and element.markdown.splitlines()[1].startswith("## ")


def test_math_issues_reach_the_element_map(make_block, make_page):
    lesson = assemble([make_page(1, [make_block(r"Bad $\frac{1}{2$ span.", order=0)])])
    assert lesson.element_map()[0]["math_issues"] == ["unbalanced_braces"]
