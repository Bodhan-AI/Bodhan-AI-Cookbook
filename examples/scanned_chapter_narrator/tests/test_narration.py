"""Unit tests for equation math verbalization and narration planning."""

from bodhan_lesson.narration import clean_spoken_text, narration_planner, verbalize_equation_math
from bodhan_lesson.schemas import BlockType, LessonSection, OCRBlock


def test_clean_spoken_text_sanitization():
    raw_text = "Refer to http://hyperphysics.phy-astr.gsu.edu for details. The magnetic field is \\mathbf{B} = 10^{-5} T."
    cleaned = clean_spoken_text(raw_text)
    assert "http" not in cleaned
    assert "hyperphysics" not in cleaned
    assert "10 to the power of -5" in cleaned
    assert "\\mathbf" not in cleaned
    assert "B" in cleaned


def test_verbalize_equation_math():
    eq1 = "$$\\angle AOB + \\angle BOC = 180^\\circ$$"
    spoken1 = verbalize_equation_math(eq1)
    assert "angle AOB" in spoken1
    assert "plus" in spoken1
    assert "angle BOC" in spoken1
    assert "equals" in spoken1
    assert "180 degrees" in spoken1

    eq2 = "$AB \\parallel CD$"
    spoken2 = verbalize_equation_math(eq2)
    assert "is parallel to" in spoken2


def test_narration_planner_grounding():
    sec = LessonSection(
        section_id="01",
        section_slug="basic-terms",
        title="Basic Terms and Definitions",
        blocks=[
            OCRBlock(
                block_id="b_01",
                page_number=1,
                normalized_type=BlockType.PARAGRAPH,
                text="An acute angle measures between zero and ninety degrees.",
            ),
            OCRBlock(
                block_id="b_02",
                page_number=1,
                normalized_type=BlockType.EQUATION,
                text="$$0 < x < 90^\\circ$$",
            ),
        ],
    )

    narration = narration_planner.generate_narration(sec, target_lang="hi", source_lang="en")
    assert "01" == narration.section_id
    assert "b_01" in narration.grounding_references
    assert "b_02" in narration.grounding_references
    assert len(narration.english_script) > 0
