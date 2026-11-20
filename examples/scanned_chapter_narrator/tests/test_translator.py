"""Unit tests for StructureShield and structure-preserving translation."""

from bodhan_lesson.translator import StructureShield


def test_structure_shield_latex_and_tables():
    shield = StructureShield()
    sample = (
        "## Theorem 6.1\n\n"
        "If two lines intersect each other, then vertically opposite angles are equal.\n\n"
        "$$\\angle AOC = \\angle BOD$$\n\n"
        "Here $x + y = 180^\\circ$ holds.\n\n"
        "| Angle | Value |\n"
        "| --- | --- |\n"
        "| AOB | 90 |"
    )

    shielded = shield.shield(sample)
    assert "__MD_HEAD_0__" in shielded
    assert "__LATEX_DISP_0__" in shielded
    assert "__LATEX_INL_1__" in shielded
    assert "__TBL_ROW_0__" in shielded

    # Simulate translation preserving tokens
    mock_translated = shielded.replace("If two lines intersect", "यदि दो रेखाएं प्रतिच्छेद करती हैं")

    unshielded, survived = shield.unshield(mock_translated)
    assert survived is True
    assert "$$\\angle AOC = \\angle BOD$$" in unshielded
    assert "$x + y = 180^\\circ$" in unshielded
    assert "| Angle | Value |" in unshielded
    assert "##" in unshielded
