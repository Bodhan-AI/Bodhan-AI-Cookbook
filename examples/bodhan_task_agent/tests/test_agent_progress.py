from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.agent.progress import (
    PLAN_JSON,
    PLAN_MD,
    PlanTracker,
    deliverable_checks,
    input_fact,
    render_markdown,
)
from bodhan_agent.core import guards
from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.budget import MinimumCost
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.plan import Deliverable, Plan
from bodhan_agent.schemas.progress import PlanView

STEPS = [
    {"id": "S1", "intent": "Read the 8 pages", "why": "All PDFs go through Bodhan OCR.", "tool": "ocr_document"},
    {"id": "S2", "intent": "Translate to English", "why": "I can only read English.", "tool": "translate_text",
     "condition": "only if the text is not English", "depends_on": ["S1"]},
    {"id": "S3", "intent": "Write an English summary", "why": "I may only write English.", "tool": "write",
     "depends_on": ["S1"]},
    {"id": "S4", "intent": "Translate the summary into Tamil", "why": "Tamil must come from Bodhan.",
     "tool": "translate_text", "depends_on": ["S3"]},
    {"id": "S5", "intent": "Speak the Tamil summary", "why": "Speech reads Bodhan's Tamil file.",
     "tool": "synthesize_speech", "depends_on": ["S4"]},
]  # fmt: skip


def make_plan(**changes: Any) -> Plan:
    data: dict[str, Any] = {
        "task_en": "Create a Tamil audio summary of this PDF.",
        "goal": "A spoken Tamil summary of the chapter plus the same text.",
        "input_notes": ["Language unknown until OCR."],
        "assumptions": ["About two minutes long."],
        "deliverables": [
            {"id": "D1", "kind": "audio", "language": "ta", "description": "Tamil audio summary"},
            {"id": "D2", "kind": "markdown", "language": "ta", "description": "Tamil summary text"},
        ],
        "steps": STEPS,
        "risks": [{"risk": "Speech drops words", "mitigation": "Back-transcription check with one retry."}],
        "feasibility": {"status": "feasible"},
    }
    data.update(changes)
    return Plan.model_validate(data)


@pytest.fixture
def tracker(tmp_path: Path) -> PlanTracker:
    pdf = tmp_path / "multi_page.pdf"
    pdf.write_bytes(b"%PDF")
    ledger = Ledger()
    ledger.add(Artifact(id="f1", path=str(pdf), kind="pdf", producer="user_input", pages=8, step_status="ok"))
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    minimum = MinimumCost(known_inr=1.6, unknown=["speech synthesis (length depends on earlier steps)"])
    return PlanTracker(settings, ledger, make_plan(), ["f1"], minimum)


def statuses(tracker: PlanTracker) -> dict[str, str]:
    return {step.id: step.status for step in tracker.steps}


def test_initial_view(tracker: PlanTracker) -> None:
    view = tracker.view()
    assert view.revision == 0 and view.finished is False
    assert set(statuses(tracker).values()) == {"pending"}
    assert view.input_facts == ["f1 multi_page.pdf: pdf, 8 page(s)"]
    assert view.input_notes == ["Language unknown until OCR."]
    assert view.steps[1].condition == "only if the text is not English"
    assert view.risks == [{"risk": "Speech drops words", "mitigation": "Back-transcription check with one retry."}]
    assert view.estimate is not None
    assert (view.estimate.known_cost_inr, view.estimate.budget_inr) == (1.6, 20.0)
    assert [item.state for item in view.deliverables] == ["not started", "not started"]


def test_begin_and_record_by_plan_step(tracker: PlanTracker) -> None:
    assert tracker.begin("ocr_document", "S1") == ["S1"]
    assert tracker.begin("ocr_document", "S1") == []
    assert statuses(tracker)["S1"] == "in_progress"
    assert tracker.record("ocr_document", "S1", 4, True, ["A1", "A2"]) == ["S1"]
    step = tracker.step("S1")
    assert step is not None and (step.status, step.calls, step.outputs) == ("done", [4], ["A1", "A2"])


def test_match_by_tool_prefers_ready_unconditional_step(tracker: PlanTracker) -> None:
    tracker.record("ocr_document", None, 1, True)
    tracker.record("write", None, 2, True)
    assert tracker.record("translate_text", None, 3, True, ["A4"]) == ["S4"]
    assert statuses(tracker)["S2"] == "pending"


def test_explicit_plan_step_wins_over_tool_match(tracker: PlanTracker) -> None:
    tracker.record("ocr_document", None, 1, True)
    assert tracker.record("translate_text", "S2", 2, True) == ["S2"]


def test_failure_then_success(tracker: PlanTracker) -> None:
    tracker.record("ocr_document", "S1", 4, False, error_code="rate_limited")
    step = tracker.step("S1")
    assert step is not None and (step.status, step.note) == ("failed", "ocr_document failed: rate_limited")
    tracker.record("ocr_document", None, 6, True, ["A1"])
    assert (step.status, step.calls, step.note) == ("done", [4, 6], None)


def test_failure_after_done_keeps_done(tracker: PlanTracker) -> None:
    tracker.record("ocr_document", "S1", 4, True, ["A1"])
    tracker.record("ocr_document", "S1", 6, False, error_code="timeout")
    step = tracker.step("S1")
    assert step is not None and step.status == "done"


def test_unplanned_call_is_deviation(tracker: PlanTracker) -> None:
    assert tracker.record("read", None, 6, True, ["A1"], reason="Read the OCR text") == []
    tracker.record("detect_language", "S9", 7, False, reason="Check", error_code="invalid_input")
    assert [(item.seq, item.tool, item.outcome, item.reason) for item in tracker.deviations] == [
        (6, "read", "ok", "Read the OCR text"),
        (7, "detect_language", "error invalid_input", "Check"),
    ]


def test_close_skips_open_steps(tracker: PlanTracker) -> None:
    tracker.record("ocr_document", "S1", 1, True)
    tracker.begin("write", "S3")
    changed = tracker.close()
    assert changed == ["S2", "S3", "S4", "S5"]
    notes = {step.id: step.note for step in tracker.steps}
    assert notes["S2"] == "not run" and notes["S3"] == "not run"
    assert tracker.view().finished is True


def test_failed_step_stays_failed_on_close(tracker: PlanTracker) -> None:
    tracker.record("synthesize_speech", "S5", 1, False, error_code="rate_limited")
    tracker.close()
    assert statuses(tracker)["S5"] == "failed"


def test_set_states_changes_revision_only_on_change(tracker: PlanTracker) -> None:
    assert tracker.set_states({"D1": "candidate A5 (ok)"}) is True
    revision = tracker.revision
    assert tracker.set_states({"D1": "candidate A5 (ok)"}) is False
    assert tracker.revision == revision
    assert tracker.view().deliverables[0].state == "candidate A5 (ok)"


def test_revision_increases(tracker: PlanTracker) -> None:
    tracker.begin("ocr_document", "S1")
    tracker.record("ocr_document", "S1", 1, True)
    tracker.record("read", None, 2, True)
    assert tracker.revision == 3


def test_write_creates_plan_files(tracker: PlanTracker) -> None:
    tracker.record("ocr_document", "S1", 4, True, ["A1"])
    view = tracker.write()
    out = tracker.settings.out_dir
    assert PlanView.model_validate(json.loads((out / PLAN_JSON).read_text())) == view
    assert (out / PLAN_MD).read_text(encoding="utf-8") == render_markdown(view)
    tracker.record("write", "S3", 5, True)
    tracker.write()
    assert json.loads((out / PLAN_JSON).read_text())["revision"] == tracker.revision


def test_markdown_layout(tracker: PlanTracker) -> None:
    tracker.record("ocr_document", "S1", 4, True, ["A1"])
    tracker.record("read", None, 6, True, reason="Read the OCR text")
    tracker.record("synthesize_speech", "S5", 9, False, error_code="rate_limited")
    tracker.set_states({"D1": "candidate A5 (ok)"})
    text = render_markdown(tracker.view())
    assert text.startswith("# Plan (revision 4)\n\n**Goal.** A spoken Tamil summary")
    assert (
        "- [x] **S1** `ocr_document`: Read the 8 pages\n  - why: All PDFs go through Bodhan OCR.\n  - produced: A1"
        in text
    )
    assert "- [ ] **S2** `translate_text`: Translate to English (only if the text is not English) after S1" in text
    assert "- [!] **S5** `synthesize_speech`" in text and "  - note: synthesize_speech failed: rate_limited" in text
    assert "## Calls outside the plan\n\n- seq 6 `read`: ok (Read the OCR text)" in text
    assert "- **D1** audio (ta): Tamil audio summary — candidate A5 (ok)" in text
    assert "- Speech drops words → Back-transcription check with one retry." in text
    assert "- Known Bodhan cost: Rs 1.60 of the Rs 20.00 budget" in text
    assert not guards.contains_indic(text)


def test_markdown_final_title_and_empty_sections(tmp_path: Path) -> None:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    plan = make_plan(steps=[], risks=[], assumptions=[], input_notes=[])
    tracker = PlanTracker(settings, Ledger(), plan)
    tracker.close()
    text = render_markdown(tracker.view())
    assert text.startswith("# Plan (final)")
    assert "- No steps were planned." in text
    for heading in ("## Inputs", "## Risks", "## Assumptions", "## Estimate", "## Calls outside the plan"):
        assert heading not in text


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"kind": "audio", "constraints": {"duration_s": 120, "voice_gender": "female"}},
         ["Back-transcription", "duration and carries", "±20% of 120 s", "A female voice"]),
        ({"kind": "audio"}, ["Back-transcription", "duration and carries"]),
        ({"kind": "subtitles"}, ["SRT parses", "cover at least 80%", "target language's script"]),
        ({"kind": "markdown", "constraints": {"keep_tables": True}},
         ["target language's script", "structural translation", "0.3x and 3.0x", "keeps its rows"]),
        ({"kind": "text"}, ["target language's script", "structural translation", "0.3x and 3.0x"]),
        ({"kind": "answer"}, ["English", "files produced in this run"]),
    ],
)  # fmt: skip
def test_deliverable_checks(item: dict[str, Any], expected: list[str]) -> None:
    checks = deliverable_checks(Deliverable(id="D1", description="x", **item))
    assert len(checks) == len(expected)
    assert all(fragment in check for fragment, check in zip(expected, checks, strict=True))


def test_input_fact_lists_unusable_inputs(tmp_path: Path) -> None:
    video = Artifact(
        id="f2", path=str(tmp_path / "clip.mp4"), kind="video", producer="user_input", duration_s=12.5,
        step_status="failed", note="has no audio track",
    )  # fmt: skip
    assert input_fact(video) == "f2 clip.mp4: video, 12.5 s, not usable: has no audio track"
    text = Artifact(
        id="f3", path=str(tmp_path / "a.txt"), kind="text", producer="user_input", script="Tamil", language="ta",
        indic_text=True, step_status="ok",
    )  # fmt: skip
    assert input_fact(text) == "f3 a.txt: text, Tamil script, language ta, Indian-language text"


def test_view_is_a_copy(tracker: PlanTracker) -> None:
    view = tracker.view()
    view.steps[0].status = "done"
    assert tracker.step("S1") is not None and tracker.step("S1").status == "pending"  # type: ignore[union-attr]
