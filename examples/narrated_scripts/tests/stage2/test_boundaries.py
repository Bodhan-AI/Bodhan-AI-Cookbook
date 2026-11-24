import json
from dataclasses import replace

import pytest
from PIL import Image

from backend import runner
from backend.models import OCRBlock, OCRPage, TableCell
from backend.ocr.assembly import decide_boundaries
from bodhan_lesson import main


def page(index, *blocks, status="ok"):
    page_id = f"p{index:03d}"
    numbered = tuple(replace(block, id=f"{page_id}-b{i:03d}") for i, block in enumerate(blocks, 1))
    ids = tuple(block.id for block in numbered)
    return OCRPage(page_id, index, status, blocks=numbered, raw_block_ids=ids, ordered_block_ids=ids)


def block(text, kind="paragraph", **kwargs):
    return OCRBlock("", kind, text, **kwargs)


def table(*rows):
    return block("", "table", rows=tuple(
        tuple(TableCell(value, is_header=index == 0) for value in row)
        for index, row in enumerate(rows)
    ))


def excluded_ids(decisions):
    return [block_id for decision in decisions if decision.action == "exclude"
            for block_id in decision.block_ids]


def test_recurring_footer_needs_position_as_well_as_matching_text():
    first = page(1, block("Definitions", "heading"), block("Classroom notes"),
                 block("First example."), block("Classroom notes", "footer"))
    second = page(2, block("Practice", "heading"), block("Classroom notes"),
                  block("  CLASSROOM   NOTES ", "footer"))
    third = page(3, block("Review", "heading"), block("Classroom notes"),
                 block("A different ending."))
    decisions = decide_boundaries((third, first, second))

    assert excluded_ids(decisions) == ["p001-b004", "p002-b003"]
    assert decisions[0].page_ids == ("p001",)
    assert "bottom" in decisions[0].reason
    assert decisions[-1].block_ids == ("p002-b002", "p003-b001")


def test_folios_need_context_and_do_not_remove_numbered_exercises():
    first = page(1, block("5. List the angles.", "list"), block("Classroom notes"), block("१२"))
    second = page(2, block("6. Measure them.", "list"), block("Classroom notes"), block("१३"))
    third = page(3, block("7. Check your work.", "list"), block("9"))
    fourth = page(4, block("Practice", "heading"), block("9", "list"))
    fifth = page(5, block("Results", "heading"), block("10"))

    decisions = decide_boundaries((first, second, third, fourth, fifth))

    assert excluded_ids(decisions) == ["p001-b002", "p002-b002", "p001-b003", "p002-b003"]
    assert "increments" in decisions[2].reason


def test_explicit_folio_counts_only_at_page_edge():
    first = page(1, block("Definitions", "heading"), block("4", "page_number"),
                 block("An example."), block("25", "page_number"))

    decisions = decide_boundaries((first,))

    assert excluded_ids(decisions) == ["p001-b004"]
    assert "OCR identifies" in decisions[0].reason


@pytest.mark.parametrize(("left", "right", "kind", "action"), [
    ("The two angles add up to", "one hundred eighty degrees.", "paragraph", "join_prose"),
    ("इन कोणों का योग", "एक सरल कोण है।", "paragraph", "keep"),
    ("इन कोणों का", "योग एक सरल कोण है।", "paragraph", "join_prose"),
    ("The two angles add up to", "Review", "heading", "keep"),
    ("The two angles add up to", "review", "list", "keep"),
    ("The two angles add up to", "1. review", "paragraph", "keep"),
    ("The angles add up.", "now measure them.", "paragraph", "keep"),
    ("The angles add up", "Now measure them.", "paragraph", "keep"),
    ("The angles satisfy\n$$x + y = 180$$", "where x is acute.", "paragraph", "keep"),
])
def test_prose_join_needs_continuation(left, right, kind, action):
    first = page(1, block(left), block("Introduction", "heading"))
    first = replace(first, ordered_block_ids=first.ordered_block_ids[::-1])
    second = page(2, block(right, kind))

    [decision] = decide_boundaries((first, second))

    assert decision.action == action
    assert decision.block_ids == ("p001-b001", "p002-b001")
    assert decision.drop_repeated_header is None


def test_quarantined_edge_prevents_prose_join():
    first = page(1, block("The angles add up to"), block("Unreadable", flags=("quarantined",)),
                 status="partial")
    second = page(2, block("one hundred eighty degrees."))
    [decision] = decide_boundaries((first, second))

    assert decision.action == "keep"
    assert decision.block_ids == ("p001-b002", "p002-b001")
    assert "quarantined" in decision.reason


def test_table_join_requests_one_repeated_header_removal():
    first = page(1, table(("Angle", "Degrees"), ("X", "65")))
    second = page(2, table(("Angle", "Degrees"), ("Y", "115")))
    [decision] = decide_boundaries((first, second))

    assert decision.action == "join_table"
    assert decision.block_ids == ("p001-b001", "p002-b001")
    assert decision.drop_repeated_header is True
    assert second.blocks[0].rows[0][0].text == "Angle"


@pytest.mark.parametrize("right", [
    table(("Angle", "Degrees", "Type"), ("Y", "115", "Obtuse")),
    table(("Angle", "Radians"), ("Y", "2")),
    block("", "table", rows=((TableCell("Angle", False), TableCell("Degrees", False)),
                              (TableCell("Y", False), TableCell("115", False)))),
])
def test_ambiguous_tables_stay_separate(right):
    first = page(1, table(("Angle", "Degrees"), ("X", "65")))
    second = page(2, right)
    [decision] = decide_boundaries((first, second))

    assert decision.action == "keep"
    assert decision.drop_repeated_header is None


def test_table_join_cannot_cross_failed_or_missing_page():
    first = page(1, table(("Angle", "Degrees"), ("X", "65")))
    failed = page(2, status="failed")
    third = page(3, table(("Angle", "Degrees"), ("Y", "115")))
    decisions = decide_boundaries((first, failed, third))

    assert [(decision.action, decision.page_ids, decision.block_ids) for decision in decisions] == [
        ("keep", ("p001", "p002"), ("p001-b001",)),
        ("keep", ("p002", "p003"), ("p003-b001",)),
    ]
    [gap] = decide_boundaries((first, third))
    assert gap.action == "keep"
    assert "missing or failed" in gap.reason


def test_cli_writes_boundary_decisions_after_layout(tmp_path, monkeypatch):
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
                 "--source-lang", "en", "--until", "boundaries"]) == 0

    decisions = json.loads((out / "work/boundaries.json").read_text(encoding="utf-8"))
    assert [(decision["action"], decision["block_ids"]) for decision in decisions] == [
        ("exclude", ["p001-b002"]),
        ("exclude", ["p002-b002"]),
        ("join_prose", ["p001-b001", "p002-b001"]),
    ]
    assert (out / "work/layout.json").is_file()
    assert (out / "source/pages/p002.json").is_file()
