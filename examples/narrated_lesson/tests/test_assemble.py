"""Unit tests for lesson assembly from cleaned pages."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_lesson.assemble import (
    assemble_lesson,
    block_to_markdown,
    join_page_markdown,
    should_join_across_pages,
)


def _write_clean(
    pages_dir: Path,
    stem: str,
    blocks: list[dict],
) -> None:
    path = pages_dir / f"{stem}.clean.json"
    path.write_text(
        json.dumps(
            {
                "page_image": f"{stem}.png",
                "repair": "skipped",
                "blocks": blocks,
                "content": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_block_to_markdown_headings_and_skip_empty() -> None:
    assert block_to_markdown(
        {"label": "Chapter-title", "text": "LINES AND ANGLES"}
    ) == "# LINES AND ANGLES"
    assert block_to_markdown(
        {"label": "Section-title", "text": "6.1 Introduction"}
    ) == "## 6.1 Introduction"
    assert block_to_markdown({"label": "Paragraph", "text": "Hello"}) == "Hello"
    assert block_to_markdown({"label": "Diagram", "text": ""}) is None


def test_should_join_across_pages() -> None:
    assert should_join_across_pages("formed between the", "horizontal line.") is True
    assert should_join_across_pages("End of sentence.", "New paragraph.") is False
    assert should_join_across_pages("word-", "break continues") is True
    assert should_join_across_pages("angles,", "such as acute") is True


def test_join_page_markdown_stitches_mid_sentence() -> None:
    joined = join_page_markdown(
        [
            "formed between the horizontal and the",
            "line of sight.",
        ]
    )
    assert "the line of sight." in joined
    assert "\n\nline of sight" not in joined


def test_assemble_lesson_writes_src_and_trace(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    source = tmp_path / "source"
    pages.mkdir()
    source.mkdir()

    _write_clean(
        pages,
        "page_001",
        [
            {
                "id": "1",
                "label": "Chapter-title",
                "text": "LINES AND ANGLES",
                "flags": [],
            },
            {
                "id": "2",
                "label": "Section-title",
                "text": "6.1 Introduction",
                "flags": [],
            },
            {
                "id": "3",
                "label": "Paragraph",
                "text": "In Chapter 5, you have studied axioms.",
                "flags": [],
            },
        ],
    )
    _write_clean(
        pages,
        "page_002",
        [
            {
                "id": "0",
                "label": "Paragraph",
                "text": "More body text after a full stop.",
                "flags": [],
            },
            {
                "id": "1",
                "label": "Equation",
                "text": "$$ x + y = 90 $$",
                "flags": [],
            },
            {
                "id": "2",
                "label": "Table",
                "text": "<table><tr><td>A</td></tr></table>",
                "flags": [],
            },
            {
                "id": "3",
                "label": "Image-caption",
                "text": "Fig. 6.1 : Types of Angles",
                "flags": [],
            },
        ],
    )

    result = assemble_lesson(pages, source_dir=source)
    assert result["status"] == "ok"
    lesson = Path(result["lesson_path"]).read_text(encoding="utf-8")
    assert "# LINES AND ANGLES" in lesson
    assert "## 6.1 Introduction" in lesson
    assert "$$ x + y = 90 $$" in lesson
    assert "<table><tr><td>A</td></tr></table>" in lesson
    assert "Fig. 6.1 : Types of Angles" in lesson
    assert "axioms.\n\nMore body text" in lesson
    assert "should not appear if present" not in lesson

    assembly = json.loads(Path(result["assembly_path"]).read_text(encoding="utf-8"))
    assert assembly["ok"] == 2
    assert assembly["pages"][0]["block_count"] == 3


def test_assemble_joins_split_sentence(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    source = tmp_path / "source"
    pages.mkdir()
    source.mkdir()
    _write_clean(
        pages,
        "page_001",
        [
            {
                "id": "0",
                "label": "Paragraph",
                "text": "formed between the horizontal and the",
                "flags": [],
            }
        ],
    )
    _write_clean(
        pages,
        "page_002",
        [
            {
                "id": "0",
                "label": "Paragraph",
                "text": "line of sight. Next sentence.",
                "flags": [],
            }
        ],
    )
    result = assemble_lesson(pages, source_dir=source)
    lesson = Path(result["lesson_path"]).read_text(encoding="utf-8")
    assert "the line of sight." in lesson
    assert "\n\nline of sight" not in lesson


def test_assemble_no_clean_pages(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    source = tmp_path / "source"
    pages.mkdir()
    source.mkdir()
    result = assemble_lesson(pages, source_dir=source)
    assert result["status"] == "error"
    assert Path(result["lesson_path"]).read_text(encoding="utf-8") == ""
