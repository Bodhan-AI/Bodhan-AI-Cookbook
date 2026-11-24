import json
import re
from dataclasses import replace

import pytest
from PIL import Image

from backend import runner
from backend.models import ListItem, SourceBlock, SourceChapter, SourceSection, TableCell, UnavailableContent
from backend.translation.provider import ModelTranslation, TranslationResponse
from backend.translation.segments import REQUEST_RESERVE, TOKEN, plan_units
from backend.translation.translate import check_structure, translate_plan
from bodhan_lesson import main


def block(number, kind, text="", **fields):
    block_id = f"p001-b{number:03d}"
    return SourceBlock(block_id, kind, (block_id,), text, **fields)


def chapter_with(*blocks, source="en"):
    heading = next((entry for entry in blocks if entry.kind == "heading"), None)
    return SourceChapter(source, (SourceSection(
        "s001", heading.heading_level if heading else None, heading.id if heading else None, None, blocks,
    ),))


def pass_through(unit_id, source, target, payload):
    return TranslationResponse(payload)


def replace_units(run, **texts):
    [target] = run.targets
    return replace(run, targets=(replace(target, units=tuple(
        replace(unit, text=texts.get(unit.unit_id, unit.text)) for unit in target.units
    )),))


def test_swapped_cell_values_fail_individually_and_keep_the_table_shape():
    table = block(1, "table", rows=((TableCell("12 cm", True), TableCell("18 cm", True)),))
    chapter = chapter_with(table)
    plan = plan_units(chapter)
    run = translate_plan(plan, ("hi",), pass_through)
    first, second = (unit.id for unit in plan.units)
    swapped = replace_units(run, **{first: "18 cm", second: "12 cm"})
    retried = []

    def reject(unit_id, source, target, payload):
        retried.append((unit_id, payload))
        return TranslationResponse("माप", (f"a{len(retried):06d}",))

    [lesson] = check_structure(chapter, plan, swapped, reject).lessons

    first_result, second_result = lesson.qa.sections[0].units
    assert [unit.disposition for unit in (first_result, second_result)] == ["exhausted", "exhausted"]
    assert first_result.issues[0].expected == {"12 cm": 1}
    assert first_result.issues[0].observed == {"18 cm": 1}
    assert second_result.issues[0].expected == {"18 cm": 1}
    assert second_result.issues[0].observed == {"12 cm": 1}
    assert retried == [(first, '<x id="1"/>'), (second, '<x id="1"/>')]
    assert all(not unit.eligible and unit.text is None for unit in (first_result, second_result))
    assert "| --- | --- |" in lesson.markdown
    assert lesson.markdown.count("Translation unavailable") == 2


@pytest.mark.parametrize("copies", [0, 2])
def test_missing_or_duplicate_placeholder_recovers_with_one_retry(copies):
    chapter = chapter_with(block(1, "paragraph", "See Fig. 6.2."))
    plan = plan_units(chapter)

    def damaged(unit_id, source, target, payload):
        token = TOKEN.search(payload)[0]
        return TranslationResponse("देखें " + " ".join([token] * copies), ("a000001",))

    calls = []

    def repair(unit_id, source, target, payload):
        calls.append((unit_id, payload))
        return TranslationResponse('देखें <x id="1"/>।', ("a000002",))

    run = translate_plan(plan, ("hi",), damaged)
    [lesson] = check_structure(chapter, plan, run, repair).lessons
    [unit] = lesson.qa.sections[0].units

    assert calls == [("p001-b001", 'See <x id="1"/>.')]
    assert unit.issues[0].check == "placeholders"
    assert unit.issues[0].observed == ({next(iter(unit.issues[0].expected)): 2} if copies else {})
    assert unit.disposition == "recovered" and unit.eligible
    assert unit.retry_attempt_ids == ("a000002",)
    assert unit.text == "देखें Fig. 6.2।"
    assert lesson.markdown == "देखें Fig. 6.2।\n"
    assert lesson.qa.status == "completed"


def test_changed_inline_and_display_latex_are_identified():
    chapter = chapter_with(
        block(1, "paragraph", "Let $x + 2$ be fixed."),
        block(2, "equation", latex=r"\frac{1}{2}"),
    )
    plan = plan_units(chapter)
    original = translate_plan(plan, ("hi",), pass_through)
    damaged = replace_units(original, **{"p001-b001": "मान लें $x + 3$।", "p001-b002": r"\frac{2}{3}"})
    calls = []

    def repair(unit_id, source, target, payload):
        calls.append(unit_id)
        return TranslationResponse('मान लें <x id="1"/>।')

    [lesson] = check_structure(chapter, plan, damaged, repair).lessons
    inline, display = lesson.qa.sections[0].units

    assert calls == ["p001-b001"]
    assert inline.issues[0].expected == {"$x + 2$": 1}
    assert inline.text == "मान लें $x + 2$।"
    assert display.issues[0].expected == r"\frac{1}{2}"
    assert display.issues[0].observed == r"\frac{2}{3}"
    assert display.disposition == "unavailable" and not display.eligible
    assert "$x + 2$" in lesson.markdown and r"\frac" not in lesson.markdown
    assert "Translation unavailable: p001-b002" in lesson.markdown


def test_heading_syntax_is_retried_and_prose_and_cell_controls_are_escaped():
    chapter = chapter_with(
        block(1, "heading", "Heading", heading_level=2),
        block(2, "paragraph", "Original explanation."),
        block(3, "table", rows=((TableCell("Left", True), TableCell("Right", True)),)),
        block(4, "list", items=(ListItem("3.", "Original step."),)),
        block(5, "equation", latex=r"x_1 + x_2"),
    )
    plan = plan_units(chapter)
    outputs = {
        "p001-b001": "# नया शीर्षक",
        "p001-b002": "पंक्ति\n\n# अतिरिक्त\n\n<script> & [कड़ी](x) *जोर*",
        "p001-b003:r01:c01": "बायाँ | दायाँ **चिह्न**",
        "p001-b003:r01:c02": "दायाँ",
        "p001-b004:i01": "- सूची के अंदर",
    }

    def translate(unit_id, source, target, payload):
        return TranslationResponse(outputs[unit_id])

    def repair(unit_id, source, target, payload):
        assert unit_id == "p001-b001"
        assert payload == "<segment>Heading</segment>"
        return TranslationResponse("<segment>सही शीर्षक</segment>")

    run = translate_plan(plan, ("hi",), translate)
    [lesson] = check_structure(chapter, plan, run, repair).lessons

    assert lesson.qa.sections[0].units[0].issues[0].check == "heading"
    assert [line for line in lesson.markdown.splitlines() if line.startswith("#")] == ["## सही शीर्षक"]
    assert r"पंक्ति \# अतिरिक्त \<script\> \& \[कड़ी\](x) \*जोर\*" in lesson.markdown
    assert r"| बायाँ \| दायाँ \*\*चिह्न\*\* | दायाँ |" in lesson.markdown
    assert r"3. \- सूची के अंदर" in lesson.markdown
    assert "$$\nx_1 + x_2\n$$" in lesson.markdown


def test_exhausted_repair_keeps_other_sections_and_source_gaps():
    intro = block(1, "paragraph", "Original explanation.")
    heading = block(2, "heading", "Later heading", heading_level=2)
    note = block(3, "paragraph", "See Fig. 6.2.")
    gap = UnavailableContent("p002", "p002", "OCR unavailable.")
    chapter = SourceChapter("en", (
        SourceSection("s001", None, None, None, (intro,)),
        SourceSection("s002", 2, heading.id, None, (heading, note, gap)),
    ))
    plan = plan_units(chapter)
    outcomes = {intro.id: "मान्य अनुच्छेद।", heading.id: "शीर्षक", note.id: "देखें।"}

    def translate(unit_id, source, target, payload):
        return TranslationResponse(outcomes[unit_id])

    calls = []

    def reject(unit_id, source, target, payload):
        calls.append(unit_id)
        return TranslationResponse("देखें।", ("a000001",))

    [lesson] = check_structure(chapter, plan, translate_plan(plan, ("hi",), translate), reject).lessons

    first, partial = lesson.qa.sections
    assert calls == [note.id]
    assert first.status == "completed" and first.units[0].eligible
    assert partial.status == lesson.qa.status == "completed_with_misses"
    assert partial.unavailable_source_ids == ("p002",)
    assert partial.units[1].disposition == "exhausted"
    assert not partial.units[1].eligible
    assert partial.units[1].retry_issues[0].check == "placeholders"
    assert "मान्य अनुच्छेद।\n\n## शीर्षक" in lesson.markdown
    assert "Translation unavailable: p001-b003" in lesson.markdown
    assert "> [Unavailable page p002]" in lesson.markdown


def test_repaired_shared_pivot_is_reused_by_both_targets():
    chapter = chapter_with(block(1, "paragraph", "लंबाई 12 cm है।"), source="hi")
    plan = plan_units(chapter)

    def initial(unit_id, source, target, payload):
        assert target == "en"
        return TranslationResponse("The length.", ("a000001",))

    calls = []

    def repair(unit_id, source, target, payload):
        calls.append((source, target, payload))
        attempt_id = f"a{len(calls) + 1:06d}"
        if target == "en":
            return TranslationResponse('It is <x id="1"/> long.', (attempt_id,))
        [token] = re.findall(r'<x id="\d+"/>', payload)
        return TranslationResponse(f"{token}।", (attempt_id,))

    original = translate_plan(plan, ("ta", "bn"), initial)
    checked = check_structure(chapter, plan, original, repair)
    assert [(source, target) for source, target, _ in calls] == [("hi", "en"), ("en", "ta"), ("en", "bn")]
    [pivot] = checked.pivot_repairs
    assert pivot.text == "It is 12 cm long."
    for lesson in checked.lessons:
        [unit] = lesson.qa.sections[0].units
        assert unit.disposition == "recovered" and unit.text == "12 cm।"
        assert unit.retry_attempt_ids[0] == pivot.legs[0].attempt_ids[0]


def test_coverage_uses_canonical_addresses_and_preserves_canonical_order():
    chapter = chapter_with(
        block(1, "heading", "Original", heading_level=2),
        block(2, "table", rows=((TableCell("first", True), TableCell("second", True)),)),
    )
    plan = plan_units(chapter)
    original = translate_plan(plan, ("hi",), pass_through)
    [target] = original.targets
    title, first, second = target.units
    duplicate = replace(title, unit_id="unknown")
    wrong_address = replace(first, address=second.address)
    run = replace(original, targets=(replace(target, units=(wrong_address, title, duplicate)),))
    calls = []

    def repair(unit_id, source, target, payload):
        calls.append(unit_id)
        return TranslationResponse("<segment>कोशिका</segment>")

    [lesson] = check_structure(chapter, plan, run, repair).lessons
    _, first_result, second_result = lesson.qa.sections[0].units

    assert calls == [first.unit_id, second.unit_id]
    assert first_result.issues[0].expected["address"]["column"] == 1
    assert first_result.issues[0].observed["address"]["column"] == 2
    assert second_result.issues[0].check == "coverage"
    assert lesson.qa.issues[0].observed == "unknown"
    assert lesson.markdown.startswith("## Original\n\n| कोशिका | कोशिका |")


def test_split_slot_keeps_valid_parts_around_an_unavailable_part():
    chapter = chapter_with(block(1, "paragraph", "alpha beta gamma delta " * 12))
    plan = plan_units(chapter, context_limit=REQUEST_RESERVE + 2 + 3 * 64)
    first, second, *later = plan.units
    calls = []

    def translate(unit_id, source, target, payload):
        return TranslationResponse("" if unit_id == second.id else payload)

    def reject(unit_id, source, target, payload):
        calls.append(unit_id)
        return TranslationResponse("")

    [lesson] = check_structure(chapter, plan, translate_plan(plan, ("hi",), translate), reject).lessons
    results = lesson.qa.sections[0].units

    assert calls == [second.id]
    assert results[0].eligible and results[0].join_after == first.join_after
    assert results[1].parent_id == first.parent_id and not results[1].eligible
    assert all(result.eligible for result in results[2:])
    assert lesson.markdown.startswith(first.payload + first.join_after)
    assert f"Translation unavailable: {second.id}" in lesson.markdown
    assert later[-1].payload.strip() in lesson.markdown


def test_translation_qa_checkpoint_writes_markdown_and_repair_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    monkeypatch.delenv("BODHAN_TRANSLATE_GGUF_URL", raising=False)
    scans = tmp_path / "scans"
    scans.mkdir()
    with Image.new("RGB", (200, 300), "white") as image:
        image.save(scans / "page-1.png")

    def recognize(self, png, options):
        return {"markdown": "# Lines AND Angles\n\nSee Fig. 6.2."}

    def translate(self, text, target):
        if text == "Lines AND Angles":
            return ModelTranslation("रेखाएँ और कोण")
        if '<x id="1"/>' in text:
            return ModelTranslation('देखें <x id="1"/>।')
        assert TOKEN.search(text)
        return ModelTranslation("देखें।")

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner.LocalTranslate, "__call__", translate)
    out = tmp_path / "out"

    assert main(["--input", str(scans), "--out", str(out), "--langs", "hi",
                 "--source-lang", "en", "--until", "translation-qa"]) == 0
    target = out / "hi"
    qa = json.loads((target / "translation-qa.json").read_text(encoding="utf-8"))
    segments = json.loads((target / "segments.json").read_text(encoding="utf-8"))
    assert qa["status"] == "completed"
    assert [unit["disposition"] for unit in qa["sections"][0]["units"]] == ["accepted", "recovered"]
    assert segments["units"][1]["text"] is None
    assert qa["attempts"][0]["id"] == qa["sections"][0]["units"][1]["retry_attempt_ids"][0]
    assert (target / "lesson.hi.md").read_text(encoding="utf-8") == "# रेखाएँ और कोण\n\nदेखें Fig. 6.2।\n"
