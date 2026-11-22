import pytest

from bodhan_lesson.stages.cleanup import block_rules as br


def test_rule1_decimal_cdot(make_block):
    block = br.apply_decimal_cdot(make_block(r"Length is 3\cdot2 cm"))
    assert block.text == "Length is 3.2 cm"
    assert "rule1_decimal_cdot" in block.rules_applied


def test_rule1_keeps_multiplication_in_pure_math(make_block):
    block = make_block(r"3\cdot2 = 6", type="Equation")
    assert br.apply_decimal_cdot(block).text == r"3\cdot2 = 6"


def test_rule4_example_number(make_block):
    block = br.apply_example_number(make_block("2 Find four angles formed."))
    assert block.text == "**Example 2:** Find four angles formed."
    assert block.label == "Example"


def test_rule4_needs_capital_after_number(make_block):
    assert br.apply_example_number(make_block("2 apples")).text == "2 apples"


def test_rule5_low_confidence_alone_is_not_doubtful(make_block):
    block = br.apply_doubtful_flag(make_block("(i) acute angle : $0^{\circ} < x < 90^{\circ}$", conf=0.5))
    assert not block.doubtful


def test_rule5_low_confidence_counts_with_another_signal(make_block):
    block = br.apply_doubtful_flag(make_block(r"$rac{1}{2$ and more clean words here", conf=0.5))
    assert block.doubtful
    assert "latex_unbalanced" in block.doubtful_reason and "low_conf=0.50" in block.doubtful_reason


def test_rule5_flags_junk_symbols(make_block):
    assert br.apply_doubtful_flag(make_block("§¶@#^~|\ ¤¥£ word", conf=0.9)).doubtful


def test_rule5_flags_an_isolated_printed_code(make_block):
    block = br.apply_doubtful_flag(make_block(r"$0962\mathrm{CH}06$", conf=0.9))
    assert block.doubtful and "isolated_code_token" in block.doubtful_reason


@pytest.mark.parametrize("text", ["$x$", "$AB$", r"$\angle ABC$", "$x = 90$", "$90^\\circ$", "Fig 6.2 and $AB$"])
def test_rule5_isolated_code_rule_leaves_real_math_alone(make_block, text):
    assert "isolated_code_token" not in br.doubtful_reasons(make_block(text, conf=0.9))


def test_rule5_flags_replacement_character(make_block):
    assert br.apply_doubtful_flag(make_block("angle � measure of the line")).doubtful


def test_rule5_flags_garbled_latex(make_block):
    block = br.apply_doubtful_flag(
        make_block(r"$$3\cdot\overset{\dot{7}}{87}$$", type="Equation", bbox=(0, 0, 100, 20))
    )
    assert block.doubtful and "garbled_overset_dot" in block.doubtful_reason


def test_rule5_flags_unbalanced_braces(make_block):
    assert br.apply_doubtful_flag(make_block(r"$\frac{1}{2$", bbox=(0, 0, 100, 20))).doubtful


def test_rule5_leaves_clean_block_alone(make_block):
    block = br.apply_doubtful_flag(make_block("A reasonably long clean sentence here."))
    assert not block.doubtful


def test_rule5_ignores_empty_figure_text(make_block):
    assert not br.apply_doubtful_flag(make_block("", type="Picture", conf=0.2)).doubtful


def test_rule6_joins_split_word():
    assert br.fix_hyphenation("inter-\nsecting lines", set()) == "intersecting lines"


def test_rule6_keeps_known_compound():
    assert br.fix_hyphenation("non-\nintersecting", {"non-intersecting"}) == "non-intersecting"


def test_rule6_vocab_from_page(make_block):
    vocab = br.build_vocab([make_block("two non-intersecting lines")])
    assert "non-intersecting" in vocab


def test_rule7_collapses_whitespace(make_block):
    assert br.apply_whitespace_and_labels(make_block("a     b")).text == "a b"


def test_rule7_extracts_equation_label_and_answer(make_block):
    block = br.apply_whitespace_and_labels(make_block("x + y = 5 (2)"))
    assert block.text == "x + y = 5" and block.eq_label == "2"
    block = br.apply_whitespace_and_labels(make_block("The angle is 40 degrees. Ans."))
    assert block.is_answer and block.text == "The angle is 40 degrees."


def test_rule7_leaves_table_spacing(make_block):
    text = "| a     | b |\n|---|---|"
    assert br.apply_whitespace_and_labels(make_block(text)).text == text


def test_rule7b_detects_markdown_table(make_block):
    assert br.apply_table_detection(make_block("| a | b |\n|---|---|\n| 1 | 2 |")).type == "Table"


def test_rule9_normalises_angle_notation(make_block):
    assert br.apply_angle_normalisation(make_block(r"$\angle$ ABC")).text == r"$\angle \text{ABC}$"
    assert br.apply_angle_normalisation(make_block(r"\angle \mathrm{ABC} = 40", type="Equation")).text == r"\angle \text{ABC} = 40"


def test_rule11_reclassifies_prose_heavy_equation(make_block):
    block = br.apply_reclassify_equation(
        make_block(r"Find the value of $x$ if the lines are parallel", type="Equation")
    )
    assert block.type == "Text" and block.reclassified


def test_rule11_keeps_real_equation(make_block):
    block = br.apply_reclassify_equation(make_block(r"\angle ABC = \angle ABD + \angle DBC", type="Equation"))
    assert block.type == "Equation" and not block.reclassified
