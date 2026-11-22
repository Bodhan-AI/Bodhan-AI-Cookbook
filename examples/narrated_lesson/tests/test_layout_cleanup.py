"""Unit tests for deterministic OCR block cleanup."""

from __future__ import annotations

from bodhan_lesson.layout_cleanup import (
    cleanup_blocks,
    collect_chapter_titles,
    is_edition_code,
    is_url_line,
)


def test_drops_furniture_and_edition_code() -> None:
    blocks = [
        {"order": 0, "label": "Header", "type": "PageHeader", "conf": 0.9, "text": ""},
        {
            "order": 1,
            "label": "Paragraph",
            "type": "Text",
            "conf": 0.7,
            "text": "$0962\\text{CH}06$",
        },
        {
            "order": 2,
            "label": "Paragraph",
            "type": "Text",
            "conf": 0.95,
            "text": "Real body text.",
        },
        {
            "order": 3,
            "label": "Footer",
            "type": "PageFooter",
            "conf": 0.8,
            "text": "62",
        },
    ]
    result = cleanup_blocks(blocks, page_index=0)
    assert [b["id"] for b in result["kept"]] == ["2"]
    reasons = {d["id"]: d["reason"] for d in result["dropped"]}
    assert reasons["0"] == "furniture_label"
    assert reasons["1"] == "edition_code"
    assert reasons["3"] == "furniture_label"


def test_flags_low_conf_without_dropping() -> None:
    blocks = [
        {
            "order": 0,
            "label": "Image-caption",
            "type": "Caption",
            "conf": 0.4,
            "text": "Fig. 1",
        }
    ]
    result = cleanup_blocks(blocks, page_index=0, low_conf=0.5)
    assert len(result["kept"]) == 1
    assert result["kept"][0]["flags"] == ["low_conf"]
    assert result["doubtful_ids"] == ["0"]


def test_drops_running_chapter_title_on_later_pages() -> None:
    first = [
        {
            "order": 0,
            "label": "Chapter-title",
            "type": "Title",
            "conf": 0.9,
            "text": "LINES AND ANGLES",
        }
    ]
    titles = collect_chapter_titles(first)
    later = [
        {
            "order": 0,
            "label": "Chapter-title",
            "type": "Title",
            "conf": 0.55,
            "text": "LINES AND ANGLES",
        },
        {
            "order": 1,
            "label": "Paragraph",
            "type": "Text",
            "conf": 0.9,
            "text": "Body continues.",
        },
    ]
    result = cleanup_blocks(later, page_index=2, chapter_titles=titles)
    assert [b["id"] for b in result["kept"]] == ["1"]
    assert result["dropped"][0]["reason"] == "running_chapter_title"


def test_keeps_chapter_title_on_first_page() -> None:
    blocks = [
        {
            "order": 0,
            "label": "Chapter-title",
            "type": "Title",
            "conf": 0.9,
            "text": "LINES AND ANGLES",
        }
    ]
    result = cleanup_blocks(blocks, page_index=0)
    assert [b["id"] for b in result["kept"]] == ["0"]


def test_junk_helpers() -> None:
    assert is_edition_code("0962CH06")
    assert is_edition_code("$0962\\text{CH}06$")
    assert not is_edition_code("In Chapter 5 you studied")
    assert is_url_line("www.ncert.nic.in")
    assert not is_url_line("see Fig. 6.1")
