"""Unit tests for Document IR schemas."""

from bodhan_lesson.schemas import (
    BlockType,
    DocumentIR,
    LessonSection,
    OCRBlock,
)


def test_ocr_block_instantiation():
    block = OCRBlock(
        block_id="b_01_0",
        order=0,
        page_number=1,
        raw_type="SectionHeader",
        normalized_type=BlockType.SECTION_HEADER,
        text="# Chapter 6",
        bbox_xyxy=[10.0, 20.0, 100.0, 50.0],
        confidence=0.98,
    )
    assert block.block_id == "b_01_0"
    assert block.normalized_type == BlockType.SECTION_HEADER
    assert not block.is_doubtful


def test_document_ir_serialization():
    block = OCRBlock(
        block_id="b_01_0",
        order=0,
        page_number=1,
        text="Hello world",
    )
    sec = LessonSection(
        section_id="01",
        section_slug="intro",
        title="Introduction",
        blocks=[block],
        source_markdown="## Introduction\nHello world",
        word_count=2,
    )
    doc_ir = DocumentIR(
        source_language="en",
        total_pages=1,
        sections=[sec],
    )
    json_str = doc_ir.model_dump_json()
    assert "Introduction" in json_str
    assert "Hello world" in json_str
