import json

from PIL import Image

from backend import runner
from backend.models import ListItem, OCRBlock, OCRPage, TableCell, UnavailableContent
from backend.ocr.assembly import build_chapter, decide_boundaries
from backend.ocr.render import render_markdown
from bodhan_lesson import main


def page(index, *blocks, status="ok", reason=None):
    ids = tuple(block.id for block in blocks)
    return OCRPage(f"p{index:03d}", index, status, blocks=blocks, raw_block_ids=ids,
                   ordered_block_ids=ids, reason=reason)


def test_intro_and_heading_hierarchy_preserve_order():
    first = page(
        1, OCRBlock("p001-b001", "paragraph", "Before the title."),
        OCRBlock("p001-b002", "heading", "Angles", heading_level=1),
        OCRBlock("p001-b003", "heading", "Terms", heading_level=2),
        OCRBlock("p001-b004", "heading", "Acute angles", heading_level=3),
        OCRBlock("p001-b005", "heading", "Exercises", heading_level=2),
    )
    chapter = build_chapter((first,), (), "en")

    assert chapter.source_lang == "en"
    assert [(section.id, section.heading_level, section.parent_id) for section in chapter.sections] == [
        ("s001", None, None),
        ("s002", 1, None),
        ("s003", 2, "s002"),
        ("s004", 3, "s003"),
        ("s005", 2, "s002"),
    ]
    assert chapter.sections[0].heading_block_id is None
    assert chapter.sections[1].heading_block_id == "p001-b002"
    assert render_markdown(chapter) == (
        "Before the title.\n\n# Angles\n\n## Terms\n\n### Acute angles\n\n## Exercises\n"
    )


def test_prose_merges_once_with_all_contributing_ids_and_excluded_footer():
    pages = (
        page(1, OCRBlock("p001-b001", "paragraph", "Two angles add"),
             OCRBlock("p001-b002", "footer", "Classroom notes")),
        page(2, OCRBlock("p002-b001", "paragraph", "up to"),
             OCRBlock("p002-b002", "footer", "Classroom notes")),
        page(3, OCRBlock("p003-b001", "paragraph", "one hundred eighty degrees."),
             OCRBlock("p003-b002", "footer", "Classroom notes")),
    )
    decisions = decide_boundaries(pages)
    chapter = build_chapter(pages, decisions, "en")
    [section] = chapter.sections
    [merged] = section.blocks

    assert merged.id == "p001-b001"
    assert merged.source_block_ids == ("p001-b001", "p002-b001", "p003-b001")
    assert render_markdown(chapter) == "Two angles add up to one hundred eighty degrees.\n"
    assert pages[0].blocks[1].text == "Classroom notes"


def test_caption_stays_adjacent_and_math_and_table_cells_survive():
    latex = r"\frac{1}{2}" + "\n" + r"+ \theta"
    rows = (
        (TableCell("Angle", True), TableCell("Value", True)),
        (TableCell("X | Y"), TableCell("65°")),
    )
    first = page(
        1, OCRBlock("p001-b001", "figure", ""),
        OCRBlock("p001-b002", "paragraph", "Compare the angles."),
        OCRBlock("p001-b003", "caption", "Figure 1. A ray.", caption_for="p001-b001"),
        OCRBlock("p001-b004", "equation", "", latex=latex),
        OCRBlock("p001-b005", "table", "", rows=rows),
    )
    chapter = build_chapter((first,), (), "en")
    figure, caption, paragraph, equation, table = chapter.sections[0].blocks

    assert caption.caption_for == figure.id
    assert paragraph.id == "p001-b002"
    assert equation.latex == latex
    assert table.rows == rows
    markdown = render_markdown(chapter)
    assert "[Figure]\n\nFigure 1. A ray.\n\nCompare the angles." in markdown
    assert f"$$\n{latex}\n$$" in markdown
    assert "| Angle | Value |\n| --- | --- |\n| X \\| Y | 65° |" in markdown


def test_figure_captions_follow_figure_even_when_ocr_puts_one_first():
    first = page(
        1, OCRBlock("p001-b001", "paragraph", "First paragraph."),
        OCRBlock("p001-b002", "caption", "(i) Acute angle.", caption_for="p001-b003"),
        OCRBlock("p001-b003", "figure", ""),
        OCRBlock("p001-b004", "paragraph", "Second paragraph."),
        OCRBlock("p001-b005", "caption", "", caption_for="p001-b003", flags=("quarantined",)),
        OCRBlock("p001-b006", "caption", "Fig. 6.1. Types of angles.", caption_for="p001-b003"),
        status="partial",
    )
    chapter = build_chapter((first,), (), "en")

    assert render_markdown(chapter) == (
        "First paragraph.\n\n[Figure]\n\n(i) Acute angle.\n\n"
        "> [Unavailable block p001-b005]\n\nFig. 6.1. Types of angles.\n\nSecond paragraph.\n"
    )
    gap = chapter.sections[0].blocks[3]
    assert isinstance(gap, UnavailableContent)
    assert not gap.eligible and gap.caption_for == "p001-b003"


def test_table_join_removes_one_header_and_updates_caption_target():
    header = (TableCell("Angle", True), TableCell("Degrees", True))
    first_rows = (tuple(TableCell(cell.text) for cell in header), (TableCell("X"), TableCell("65")))
    second_rows = (header, (TableCell("Y"), TableCell("115")))
    pages = (
        page(1, OCRBlock("p001-b001", "table", "", rows=first_rows)),
        page(2, OCRBlock("p002-b001", "table", "", rows=second_rows),
             OCRBlock("p002-b002", "caption", "Table 1. Adjacent angles.", caption_for="p002-b001")),
    )
    chapter = build_chapter(pages, decide_boundaries(pages), "en")
    table, caption = chapter.sections[0].blocks

    assert table.source_block_ids == ("p001-b001", "p002-b001")
    assert table.rows == (header, first_rows[1], second_rows[1])
    assert caption.caption_for == table.id == "p001-b001"
    assert render_markdown(chapter).count("| Angle | Degrees |") == 1


def test_failed_missing_and_quarantined_content_stays_visible_and_ineligible():
    pages = (
        page(1, OCRBlock("p001-b001", "paragraph", "Before the gap.")),
        page(2, status="failed", reason="The scan could not be read."),
        page(4, OCRBlock("p004-b001", "paragraph", "Unreadable OCR", flags=("quarantined",)),
             OCRBlock("p004-b002", "paragraph", "After the gap."), status="partial"),
    )
    chapter = build_chapter(pages, decide_boundaries(pages), None)
    [section] = chapter.sections

    assert chapter.source_lang is None
    assert [block.id for block in section.blocks if block.eligible] == ["p001-b001", "p004-b002"]
    failed, missing, quarantined = (b for b in section.blocks if isinstance(b, UnavailableContent))
    assert (failed.page_id, failed.reason) == ("p002", "The scan could not be read.")
    assert missing.page_id == "p003"
    assert quarantined.source_block_ids == ("p004-b001",)
    assert quarantined.flags == ("quarantined",)
    assert render_markdown(chapter, {"p004-b001": "unused"}) == (
        "Before the gap.\n\n> [Unavailable page p002]\n\n> [Unavailable page p003]\n\n"
        "> [Unavailable block p004-b001]\n\nAfter the gap.\n"
    )


def test_same_renderer_supplies_cell_and_item_text_by_structural_address():
    rows = (
        (TableCell("Angle", True), TableCell("Value", True)),
        (TableCell("X"), TableCell("65°")),
    )
    first = page(
        1, OCRBlock("p001-b001", "heading", "Angles", heading_level=2),
        OCRBlock("p001-b002", "list", "", items=(ListItem("1.", "Find X"),)),
        OCRBlock("p001-b003", "table", "", rows=rows),
    )
    chapter = build_chapter((first,), (), "en")
    assert render_markdown(chapter, {
        "p001-b001": "कोण", "p001-b002:i01": "X ज्ञात करें", "p001-b003:r02:c01": "X | Y",
    }) == "## कोण\n\n1. X ज्ञात करें\n\n| Angle | Value |\n| --- | --- |\n| X \\| Y | 65° |\n"


def test_renderer_preserves_spans_and_uses_no_data_cell_as_a_header():
    first = page(
        1, OCRBlock("p001-b001", "table", "", rows=(
            (TableCell("Group", True, colspan=2),),
            (TableCell("X"), TableCell("<Y>")),
        )),
        OCRBlock("p001-b002", "table", "", rows=((TableCell("P"), TableCell("Q")),)),
    )
    rendered = render_markdown(build_chapter((first,), (), "en"))
    assert '<th colspan="2">Group</th>' in rendered
    assert "<tr><td>X</td><td>&lt;Y&gt;</td></tr>" in rendered
    assert "|  |  |\n| --- | --- |\n| P | Q |" in rendered


def test_source_checkpoint_writes_markdown_and_chapter_from_boundary_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    texts = iter(("The angles add up to", "one hundred eighty degrees."))
    for index in (1, 2):
        with Image.new("RGB", (200, 300), "white") as image:
            image.save(tmp_path / f"page-{index}.png")

    def recognize(self, png, options):
        return {"markdown": f"{next(texts)}\n\nClassroom notes"}

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    out = tmp_path / "out"
    assert main(["--input", str(tmp_path), "--out", str(out), "--langs", "hi",
                 "--source-lang", "en", "--until", "source"]) == 0
    assert (out / "source/lesson.src.md").read_text(encoding="utf-8") == (
        "The angles add up to one hundred eighty degrees.\n"
    )
    chapter = json.loads((out / "work/chapter.json").read_text(encoding="utf-8"))
    [block] = chapter["sections"][0]["blocks"]
    assert chapter["source_lang"] == "en"
    assert block["eligible"] is True
    assert block["source_block_ids"] == ["p001-b001", "p002-b001"]
    assert (out / "work/boundaries.json").is_file()
