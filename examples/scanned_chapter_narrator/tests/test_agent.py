"""Unit tests for ChapterNarratorAgent block triage, glossary extraction, and narration planning."""

from bodhan_lesson.agent import chapter_narrator_agent
from bodhan_lesson.schemas import BlockType, LessonSection, OCRBlock


def test_agent_block_triage_margin_notes():
    blocks = [
        OCRBlock(
            block_id="b1",
            page_number=1,
            normalized_type=BlockType.PARAGRAPH,
            text="The magnetic field B inside the solenoid is uniform.",
            confidence=0.98,
            bbox_xyxy=[0.2, 0.2, 0.8, 0.3],
        ),
        OCRBlock(
            block_id="b2",
            page_number=1,
            normalized_type=BlockType.PARAGRAPH,
            text="Note: Remember to measure temperature in Kelvin.",
            confidence=0.95,
            bbox_xyxy=[0.05, 0.2, 0.15, 0.3],
        ),
        OCRBlock(
            block_id="b3",
            page_number=1,
            normalized_type=BlockType.PARAGRAPH,
            text="xyz???§§§§ corrupted text",
            confidence=0.55,
            bbox_xyxy=[0.2, 0.4, 0.8, 0.5],
        ),
    ]

    main_blocks, annotations = chapter_narrator_agent.triage_blocks(blocks)
    assert len(main_blocks) == 2
    assert len(annotations) == 1
    assert annotations[0]["block_id"] == "b2"
    assert "Remember to measure temperature in Kelvin." in annotations[0]["text"]
    # Check doubtful flag on corrupted block
    assert main_blocks[1].is_doubtful is True


def test_agent_glossary_extraction():
    text = (
        "Diamagnetism is defined as the weak repulsion of materials in a magnetic field. "
        "Paramagnetism refers to weak attraction toward stronger fields. "
        "Ferromagnetism is a permanent magnetic alignment."
    )
    glossary = chapter_narrator_agent.extract_chapter_glossary(text)
    assert len(glossary) >= 1
    # Check that key terms are extracted
    keys_lower = [k.lower() for k in glossary.keys()]
    assert any("diamagnetism" in k for k in keys_lower) or any("paramagnetism" in k for k in keys_lower)


def test_agent_plan_narration_grounded():
    sec = LessonSection(
        section_id="01",
        section_slug="diamagnetism",
        title="Diamagnetism",
        blocks=[
            OCRBlock(
                block_id="b_01",
                page_number=1,
                normalized_type=BlockType.PARAGRAPH,
                text="Diamagnetic substances are those which tend to move from stronger to weaker parts of the field.",
            ),
            OCRBlock(
                block_id="b_02",
                page_number=1,
                normalized_type=BlockType.EQUATION,
                text="$$M = C \\frac{B_0}{T}$$",
            ),
        ],
    )

    narration = chapter_narrator_agent.plan_narration(sec, code_mixed=True)
    assert len(narration) > 30
    assert "Welcome to this section on Diamagnetism" in narration or "Diamagnetism" in narration


def test_agent_plan_audio_repair():
    from bodhan_lesson.schemas import QAReportSection

    qa_fail = QAReportSection(
        section_id="01",
        language="hi",
        coverage_score=0.90,
        repetition_detected=True,
        repetition_phrases=["magnetic field magnetic field"],
        cer=0.45,
        wer=0.50,
        passed=False,
        qa_status="failed",
    )

    script = "In this section, we study the magnetic field magnetic field magnetic field and force."
    repaired = chapter_narrator_agent.plan_audio_repair(script, qa_fail)
    assert repaired != script
    # Repetition loop should be compressed
    assert "magnetic field magnetic field magnetic field" not in repaired
