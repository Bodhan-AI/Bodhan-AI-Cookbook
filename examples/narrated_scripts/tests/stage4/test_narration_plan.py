import json

import pytest
from PIL import Image

from backend import runner
from backend.audio.narration import plan_narration, validate_english_draft
from backend.models import ListItem, SourceBlock, SourceChapter, SourceSection, TableCell, UnavailableContent
from backend.translation.provider import TranslationResponse
from backend.translation.segments import plan_units
from backend.translation.translate import check_structure, translate_plan
from bodhan_lesson import main


def block(number, kind, text="", **fields):
    block_id = f"p001-b{number:03d}"
    return SourceBlock(block_id, kind, (block_id,), text, **fields)


def chapter_with(*blocks, source="en"):
    return SourceChapter(source, (SourceSection("s001", None, None, None, blocks),))


def passthrough(unit_id, source, target, payload):
    return TranslationResponse(payload)


def checked(chapter, targets=("en",), translate=passthrough):
    plan = plan_units(chapter)
    return check_structure(chapter, plan, translate_plan(plan, targets, translate), translate)


def test_table_introduces_headers_then_pairs_each_value_with_its_column():
    table = block(1, "table", rows=(
        (TableCell("Width", True), TableCell("Height", True)),
        (TableCell("12 cm"), TableCell("20 cm")),
        (TableCell("15 cm"), TableCell("24 cm")),
    ))
    chapter = chapter_with(table)
    [target] = plan_narration(chapter, checked(chapter)).targets

    assert [entry.policy for entry in target.entries] == ["table_headers", "table_row", "table_row"]
    assert [entry.text for entry in target.entries] == [
        "Width, Height", "Width: 12 cm, Height: 20 cm", "Width: 15 cm, Height: 24 cm",
    ]
    assert target.entries[1].unit_ids == tuple(table.cell_unit_id(row, column)
                                               for column in (1, 2) for row in (1, 2))
    assert all(entry.source_block_ids == (table.id,) for entry in target.entries)


@pytest.mark.parametrize(("latex", "speech"), [
    ("a/(b+c)", "a divided by open parenthesis b plus c close parenthesis."),
    (r"a/\frac{b}{c}", "a divided by open parenthesis open parenthesis b close parenthesis "
                        "divided by open parenthesis c close parenthesis close parenthesis."),
    (r" \angle \mathrm{ABC} = \angle \mathrm{ABD} + \angle \mathrm{DBC}. ",
     "angle ABC equals angle ABD plus angle DBC."),
    (r"0^{\circ} < x < 90^\circ", "0 degrees is less than x is less than 90 degrees."),
])
def test_equation_reads_the_whole_denominator_as_a_group_and_logs_unsupported_latex(latex, speech):
    chapter = chapter_with(block(1, "equation", latex=latex),
                           block(2, "equation", latex=r"\sqrt{x}"))
    [target] = plan_narration(chapter, checked(chapter)).targets
    supported, unsupported = target.entries

    assert supported.action == "translate_english"
    assert supported.text == speech
    assert unsupported.action == "skip" and unsupported.text is None
    assert "unsupported notation" in unsupported.reason


def test_figure_does_not_infer_relationship_between_known_labels():
    figure = block(1, "figure")
    caption = block(2, "caption", "Figure 1. Labels AB and CD.", caption_for=figure.id)
    chapter = chapter_with(figure, caption)

    def propose(evidence):
        assert evidence == {caption.id: caption.text}
        return json.dumps({"text": "AB is parallel to CD.", "block_ids": [caption.id]})

    [target] = plan_narration(chapter, checked(chapter), propose_figure=propose).targets
    [entry] = target.entries
    assert entry.action == "reuse_translation" and entry.policy == "figure_caption"
    assert entry.text == caption.text
    assert entry.source_block_ids == (figure.id, caption.id)
    assert entry.supporting_block_ids == (caption.id,)


@pytest.mark.parametrize(("draft", "ids"), [
    ("AB measures 12 cm.", ["p009-b999"]),
    ("AB measures 99 cm.", ["p001-b001"]),
    ("CD measures 12 cm.", ["p001-b001"]),
])
def test_english_draft_rejects_unknown_sources_or_invented_values_and_labels(draft, ids):
    evidence = {"p001-b001": "AB measures 12 cm."}
    assert validate_english_draft(json.dumps({"text": draft, "block_ids": ids}), evidence) is None


def test_figure_uses_the_checked_english_pivot_once_across_targets():
    figure = block(1, "figure")
    caption = block(2, "caption", "दो रेखाएँ मिलती हैं।", caption_for=figure.id)
    chapter = chapter_with(figure, caption, source="hi")

    def translate(unit_id, source, target, payload):
        if target == "en":
            assert payload == caption.text
            return TranslationResponse("Two lines meet.")
        assert source == "en" and payload == "Two lines meet."
        return TranslationResponse({"ta": "இரண்டு கோடுகள் சந்திக்கின்றன.", "bn": "দুটি রেখা মিলিত হয়।"}[target])

    proposals = []

    def propose(evidence):
        proposals.append(evidence)
        return json.dumps({"text": "Two lines meet.", "block_ids": [caption.id]})

    plans = plan_narration(chapter, checked(chapter, ("ta", "bn"), translate), propose_figure=propose)
    assert proposals == [{caption.id: "Two lines meet."}]
    assert [target.target_lang for target in plans.targets] == ["ta", "bn"]
    assert all(target.entries[0].action == "translate_english" for target in plans.targets)
    assert [target.entries[0].fallback_text for target in plans.targets] == [
        "இரண்டு கோடுகள் சந்திக்கின்றன.", "দুটি রেখা মিলিত হয়।",
    ]


def test_quarantined_and_failed_units_are_skipped_and_list_items_keep_index_order():
    paragraph = block(1, "paragraph", "Secret failed text")
    gap = UnavailableContent("p001-b002", "p001", "OCR block is quarantined.",
                             ("p001-b002",), ("quarantined",))
    steps = block(3, "list", items=(ListItem("3.", "First in source"), ListItem("1.", "Second in source")))
    chapter = chapter_with(paragraph, gap, steps)

    def translate(unit_id, source, target, payload):
        if unit_id == paragraph.id:
            return TranslationResponse(None, reason="The translation request failed.")
        return TranslationResponse("पहला" if unit_id == steps.item_unit_id(1) else "दूसरा")

    [target] = plan_narration(chapter, checked(chapter, ("hi",), translate)).targets
    failed, quarantined, *items = target.entries
    assert failed.action == quarantined.action == "skip"
    assert failed.reason == "The translation request failed."
    assert quarantined.reason == "OCR block is quarantined."
    assert failed.text is None and quarantined.text is None
    assert [entry.unit_ids for entry in items] == [(steps.item_unit_id(1),), (steps.item_unit_id(2),)]
    assert [(entry.action, entry.text) for entry in items] == [
        ("reuse_translation", "पहला"), ("reuse_translation", "दूसरा"),
    ]


def test_cli_writes_the_narration_plan_after_checked_translation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    monkeypatch.delenv("BODHAN_TRANSLATE_GGUF_URL", raising=False)
    scans = tmp_path / "scans"
    scans.mkdir()
    with Image.new("RGB", (200, 300), "white") as image:
        image.save(scans / "page-1.png")

    def recognize(self, png, options):
        return {"markdown": "# Geometry\n\nRead this first.\n\n$$a/(b+c)$$"}

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    out = tmp_path / "out"
    assert main(["--input", str(scans), "--out", str(out), "--langs", "en", "--source-lang", "en",
                 "--until", "narration-plan"]) == 0

    artifact = json.loads((out / "work" / "narration-plan.json").read_text(encoding="utf-8"))
    [target] = artifact["targets"]
    assert (out / "en" / "translation-qa.json").is_file()
    assert target["target_lang"] == "en"
    assert [entry["action"] for entry in target["entries"]] == [
        "reuse_translation", "reuse_translation", "translate_english",
    ]
    assert all(entry["section_id"] == "s001" for entry in target["entries"])
