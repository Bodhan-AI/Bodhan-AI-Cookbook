"""Unit tests for PageAssembler cross-page stitching and noise filtering."""

from pathlib import Path

from bodhan_lesson.assembler import assembler
from bodhan_lesson.schemas import BlockType, OCRBlock, PageResult


def test_assembler_cross_page_hyphen_stitching():
    # Page 1 ends with "trans-"
    p1 = PageResult(
        page_number=1,
        image_path=Path("p1.png"),
        raw_markdown="The line is a trans-\n",
        blocks=[
            OCRBlock(
                block_id="b_01_0",
                order=0,
                page_number=1,
                normalized_type=BlockType.PARAGRAPH,
                text="The line is a trans-",
            )
        ],
    )
    # Page 2 begins with "versal line intersecting two points."
    p2 = PageResult(
        page_number=2,
        image_path=Path("p2.png"),
        raw_markdown="versal line intersecting two points.\n",
        blocks=[
            OCRBlock(
                block_id="b_02_0",
                order=0,
                page_number=2,
                normalized_type=BlockType.PARAGRAPH,
                text="versal line intersecting two points.",
            )
        ],
    )

    doc_ir = assembler.assemble_document([p1, p2], source_language="en")
    assert "transversal line" in doc_ir.source_markdown


def test_assembler_recurring_noise_filtering():
    # 3 pages with identical website watermark
    pages = []
    for i in range(1, 4):
        pages.append(
            PageResult(
                page_number=i,
                image_path=Path(f"p{i}.png"),
                raw_markdown=f"# Heading {i}\nContent {i}\nwww.aspirationsinstitute.com",
                blocks=[
                    OCRBlock(
                        block_id=f"b_{i}_0",
                        order=0,
                        page_number=i,
                        normalized_type=BlockType.SECTION_HEADER,
                        text=f"# Heading {i}",
                        bbox_xyxy=[10, 10, 500, 40],
                    ),
                    OCRBlock(
                        block_id=f"b_{i}_1",
                        order=1,
                        page_number=i,
                        normalized_type=BlockType.PARAGRAPH,
                        text=f"Content {i}",
                        bbox_xyxy=[10, 100, 500, 200],
                    ),
                    OCRBlock(
                        block_id=f"b_{i}_2",
                        order=2,
                        page_number=i,
                        normalized_type=BlockType.PARAGRAPH,
                        text="www.aspirationsinstitute.com",
                        bbox_xyxy=[10, 950, 500, 980],  # bottom 10%
                    ),
                ],
            )
        )

    doc_ir = assembler.assemble_document(pages, source_language="en")
    assert "aspirationsinstitute.com" not in doc_ir.source_markdown
    assert "Content 1" in doc_ir.source_markdown


def test_assembler_boundary_aware_pre_heading_folding():
    # Pre-heading table & intro paragraph before Heading 1
    blocks = [
        OCRBlock(
            block_id="b0",
            page_number=1,
            normalized_type=BlockType.TABLE,
            text="| Col1 | Col2 |\n|---|---|\n| A | B |",
        ),
        OCRBlock(
            block_id="b1",
            page_number=1,
            normalized_type=BlockType.SECTION_HEADER,
            text="## 5.1 Magnetic Field Lines",
        ),
        OCRBlock(
            block_id="b2",
            page_number=1,
            normalized_type=BlockType.PARAGRAPH,
            text="Magnetic field lines form continuous closed loops.",
        ),
    ]
    p = PageResult(page_number=1, image_path=Path("p.png"), blocks=blocks)
    doc_ir = assembler.assemble_document([p], source_language="en")

    # Should NOT fabricate "Overview & Introduction"
    assert len(doc_ir.sections) == 1
    assert doc_ir.sections[0].title == "5.1 Magnetic Field Lines"
    assert "Overview & Introduction" not in [s.title for s in doc_ir.sections]
    # Pre-heading table must be preserved in section blocks
    assert any(b.normalized_type == BlockType.TABLE for b in doc_ir.sections[0].blocks)


def test_assembler_mid_book_no_headings():
    # Scan of pure narrative text without any headings
    blocks = [
        OCRBlock(
            block_id="b0",
            page_number=1,
            normalized_type=BlockType.PARAGRAPH,
            text="This is a continuing discussion from the previous chapter.",
        ),
        OCRBlock(
            block_id="b1",
            page_number=1,
            normalized_type=BlockType.PARAGRAPH,
            text="It continues with derivation of electrostatic potential.",
        ),
    ]
    p = PageResult(page_number=1, image_path=Path("p.png"), blocks=blocks)
    doc_ir = assembler.assemble_document([p], source_language="en")

    # Must produce neutral "Section 01", not an invented heading or overview
    assert len(doc_ir.sections) == 1
    assert doc_ir.sections[0].title == "Section 01"
    assert len(doc_ir.sections[0].blocks) == 2
