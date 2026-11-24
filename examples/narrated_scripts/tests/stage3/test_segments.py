import json

import pytest
from PIL import Image

from backend import runner
from backend.models import ListItem, SourceBlock, SourceChapter, SourceSection, TableCell, UnavailableContent
from backend.translation.segments import REQUEST_RESERVE, load_glossary, plan_units, protect_spans, restore_spans
from bodhan_lesson import main


def chapter_with(*blocks):
    return SourceChapter("en", (SourceSection("s002", 2, "p001-b001", None, blocks),))


def test_same_numeral_is_protected_separately_in_each_cell():
    table = SourceBlock(
        "p001-b002", "table", ("p001-b002", "p002-b001"),
        rows=((TableCell("12 cm"), TableCell("12 cm")),),
    )
    first, second = plan_units(chapter_with(table)).units

    assert (first.id, second.id) == ("p001-b002:r01:c01", "p001-b002:r01:c02")
    assert [(unit.address.row, unit.address.column) for unit in (first, second)] == [(1, 1), (1, 2)]
    for unit in (first, second):
        assert unit.address.kind == "table_cell"
        assert unit.section_id == "s002"
        assert unit.group_id == table.id
        assert unit.source_block_ids == table.source_block_ids
        assert unit.protected_spans == {unit.payload: "12 cm"}
    assert first.payload != second.payload
    with pytest.raises(ValueError, match="found 0"):
        restore_spans(second.payload, first.protected_spans)


def test_slot_order_and_unavailable_content():
    chapter = chapter_with(
        SourceBlock("p001-b001", "heading", ("p001-b001",), text="Angles", heading_level=2),
        SourceBlock("p001-b002", "list", ("p001-b002",), items=(ListItem("3.", "Find the angle."),)),
        SourceBlock("p001-b003", "figure", ("p001-b003",), text="[Figure]"),
        SourceBlock("p001-b004", "caption", ("p001-b004",), text="Fig. 3. A ray.", caption_for="p001-b003"),
        UnavailableContent("p002", "p002", "OCR failed."),
        SourceBlock("p003-b001", "equation", ("p003-b001",), latex=r"\theta + 2"),
    )

    heading, item, caption, equation = plan_units(chapter).units
    assert [(unit.id, unit.address.kind) for unit in (heading, item, caption, equation)] == [
        ("p001-b001", "heading"), ("p001-b002:i01", "list_item"),
        ("p001-b004", "caption"), ("p003-b001", "equation"),
    ]
    assert item.address.item == 1
    assert item.payload == "Find the angle."
    assert list(caption.protected_spans.values()) == ["Fig. 3"]
    assert equation.literal
    assert equation.payload == r"\theta + 2"


def test_inline_math_quantities_and_references_round_trip_byte_for_byte():
    text = r"Compare $\frac{1}{2}$, \(x + 4°\), −12.5  cm², 6°, १२ सेमी, 180 degrees, Fig. 2.4(a) and AB12."
    payload, spans = protect_spans(text, "p001-b001")

    assert list(spans.values()) == [
        r"$\frac{1}{2}$", r"\(x + 4°\)", "−12.5  cm²", "6°", "१२ सेमी", "180 degrees", "Fig. 2.4(a)", "AB12",
    ]
    assert restore_spans(payload, spans).encode("utf-8") == text.encode("utf-8")
    token = next(iter(spans))
    with pytest.raises(ValueError, match="found 2"):
        restore_spans(payload + token, spans)


def test_placeholder_like_source_reallocates_the_namespace():
    unit_id = "p001-b001"
    existing, _ = protect_spans("12 cm", unit_id)
    text = f"Keep {existing} and 12 cm."

    payload, spans = protect_spans(text, unit_id)

    assert existing in payload
    assert existing not in spans
    assert list(spans.values()) == ["12 cm"]
    assert restore_spans(payload, spans) == text


def test_oversized_prose_splits_at_whitespace_and_literal_failure_is_visible():
    sentence = r"Compare \(x + 2\) with 12 cm."
    text = " \t\n".join([sentence] * 5)
    prose = SourceBlock("p001-b001", "paragraph", ("p001-b001", "p002-b001"), text=text)
    equation = SourceBlock("p002-b002", "equation", ("p002-b002",), latex=r"\text{" + ("x" * 160) + "}")
    heading = SourceBlock("p002-b003", "heading", ("p002-b003",), text="oversized heading " * 10)

    plan = plan_units(chapter_with(prose, equation, heading), context_limit=REQUEST_RESERVE + 2 + 360)
    *parts, failed_equation, failed_heading = plan.units

    assert plan.payload_budget == 120
    assert len(parts) > 1
    for index, part in enumerate(parts, 1):
        assert part.id == f"{prose.id}:p{index:02d}"
        assert (part.parent_id, part.part) == (prose.id, index)
        assert part.source_block_ids == prose.source_block_ids
        assert part.status == "ready"
        assert len(part.payload.encode("utf-8")) <= plan.payload_budget
        assert all(literal in (r"\(x + 2\)", "12 cm") for literal in part.protected_spans.values())
    restored = "".join(restore_spans(part.payload, part.protected_spans) + part.join_after for part in parts)
    assert restored == text
    assert [unit.status for unit in (failed_equation, failed_heading)] == ["failed", "failed"]
    assert failed_equation.literal and failed_equation.payload == equation.latex
    assert failed_heading.id == heading.id
    assert "120-byte" in failed_equation.reason


def test_glossary_is_validated_and_reduces_available_context(tmp_path):
    path = tmp_path / "glossary.yaml"
    path.write_text("angle: {hi: कोण, ta: கோணம்}\nray: किरण\n", encoding="utf-8")
    chapter = chapter_with(SourceBlock("p001-b001", "paragraph", ("p001-b001",), text="An angle."))

    plan = plan_units(chapter, load_glossary(path))

    assert plan.glossary == {"angle": {"hi": "कोण", "ta": "கோணம்"}, "ray": "किरण"}
    assert plan.payload_budget < plan_units(chapter).payload_budget
    assert plan.context_limit < 32 * 1024
    path.write_text("angle: [कोण]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs a target string"):
        load_glossary(path)


def test_segments_checkpoint_writes_the_plan_after_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    scans = tmp_path / "scans"
    scans.mkdir()
    with Image.new("RGB", (200, 300), "white") as image:
        image.save(scans / "page-1.png")

    def recognize(self, png, options):
        return {"markdown": "# Angles\n\nThe length is 12 cm."}

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("angle: {hi: कोण}\n", encoding="utf-8")
    out = tmp_path / "out"

    assert main(["--input", str(scans), "--langs", "hi", "--out", str(out),
                 "--source-lang", "en", "--until", "segments", "--glossary", str(glossary)]) == 0
    assert (out / "work/chapter.json").is_file()
    assert (out / "source/lesson.src.md").is_file()
    plan = json.loads((out / "work/translation-plan.json").read_text(encoding="utf-8"))
    assert plan["source_lang"] == "en"
    assert plan["glossary"] == {"angle": {"hi": "कोण"}}
    heading, paragraph = plan["units"]
    assert [heading["address"]["kind"], paragraph["address"]["kind"]] == ["heading", "paragraph"]
    assert list(paragraph["protected_spans"].values()) == ["12 cm"]
