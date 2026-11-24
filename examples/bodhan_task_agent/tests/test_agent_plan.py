from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from bodhan_agent.schemas.plan import Plan, normalize_language


def deliverable(**overrides: Any) -> dict[str, Any]:
    return {"id": "D1", "kind": "audio", "description": "Spoken summary", **overrides}


def plan(**overrides: Any) -> dict[str, Any]:
    return {
        "task_en": "Make a Tamil audio summary",
        "goal": "Summarise the chapter and read it aloud in Tamil.",
        "deliverables": [deliverable(language="ta")],
        "feasibility": {"status": "feasible"},
        **overrides,
    }


def test_valid_tamil_mp3_deliverable() -> None:
    result = Plan.model_validate(plan(deliverables=[deliverable(language="ta", format="mp3")]))
    item = result.deliverables[0]
    assert (item.kind, item.language, item.format) == ("audio", "ta", "mp3")


def test_wav_audio_with_female_voice() -> None:
    result = Plan.model_validate(
        plan(deliverables=[deliverable(language="bn", format="wav", constraints={"voice_gender": "female"})])
    )
    assert result.deliverables[0].constraints.voice_gender == "female"


def test_missing_optional_fields_get_defaults() -> None:
    result = Plan.model_validate(plan(deliverables=[deliverable()]))
    item = result.deliverables[0]
    assert item.format == "mp3"
    assert item.language is None
    assert item.constraints.duration_s is None
    assert item.constraints.voice_gender is None
    assert item.constraints.keep_tables is False
    assert result.assumptions == []
    assert result.steps == []


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("subtitles", "srt"), ("markdown", "md"), ("text", "txt"), ("answer", None)],
)
def test_default_format_per_kind(kind: str, expected: str | None) -> None:
    result = Plan.model_validate(plan(deliverables=[deliverable(kind=kind)]))
    assert result.deliverables[0].format == expected


def test_subtitles_reject_audio_format() -> None:
    with pytest.raises(ValidationError, match="takes srt"):
        Plan.model_validate(plan(deliverables=[deliverable(kind="subtitles", format="mp3")]))


def test_answer_rejects_file_format() -> None:
    with pytest.raises(ValidationError, match="takes no format"):
        Plan.model_validate(plan(deliverables=[deliverable(kind="answer", format="txt")]))


def test_audio_constraints_rejected_on_non_audio() -> None:
    with pytest.raises(ValidationError, match="apply only to audio"):
        Plan.model_validate(plan(deliverables=[deliverable(kind="text", constraints={"duration_s": 60})]))


def test_unsupported_language_passes_schema() -> None:
    result = Plan.model_validate(plan(deliverables=[deliverable(kind="subtitles", language="fr")]))
    assert result.deliverables[0].language == "fr"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("ta", "ta"), ("HI", "hi"), ("hi-IN", "hi"), ("ks-deva", "ks-Deva"), ("mni-Beng", "mni-Beng"), ("es-419", "es")],
)
def test_language_codes_are_normalized(raw: str, expected: str) -> None:
    assert normalize_language(raw) == expected


@pytest.mark.parametrize("raw", ["Tamil", "t", "hi_IN", "ks-Devanagari", ""])
def test_invalid_language_codes_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_language(raw)


def test_blank_language_means_unset() -> None:
    result = Plan.model_validate(plan(deliverables=[deliverable(language="  ")]))
    assert result.deliverables[0].language is None


def test_long_duration_is_not_capped_by_schema() -> None:
    result = Plan.model_validate(plan(deliverables=[deliverable(constraints={"duration_s": 7200})]))
    assert result.deliverables[0].constraints.duration_s == 7200


@pytest.mark.parametrize("value", [0, -5, float("inf")])
def test_invalid_duration_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(deliverables=[deliverable(constraints={"duration_s": value})]))


def test_misspelled_constraint_rejected() -> None:
    with pytest.raises(ValidationError, match="duraton_s"):
        Plan.model_validate(plan(deliverables=[deliverable(constraints={"duraton_s": 120})]))


def test_misspelled_deliverable_field_rejected() -> None:
    with pytest.raises(ValidationError, match="langauge"):
        Plan.model_validate(plan(deliverables=[deliverable(langauge="ta")]))


def test_extra_fields_ignored_outside_deliverables() -> None:
    result = Plan.model_validate(
        plan(
            notes="extra",
            steps=[{"id": "S1", "intent": "OCR the PDF", "why": "Needed for the task.", "confidence": 0.9}],
        )
    )
    assert result.steps[0].id == "S1"


def test_empty_description_rejected() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(deliverables=[deliverable(description=" ")]))


def test_empty_step_intent_rejected() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(steps=[{"id": "S1", "intent": "", "why": "Needed for the task."}]))


@pytest.mark.parametrize("bad_id", ["d1", "D", "1", "Deliverable1"])
def test_bad_deliverable_id_rejected(bad_id: str) -> None:
    with pytest.raises(ValidationError, match="D1, D2"):
        Plan.model_validate(plan(deliverables=[deliverable(id=bad_id)]))


def test_duplicate_deliverable_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="deliverable ids must be unique"):
        Plan.model_validate(plan(deliverables=[deliverable(), deliverable(kind="text")]))


def test_duplicate_step_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="step ids must be unique"):
        Plan.model_validate(
            plan(
                steps=[
                    {"id": "S1", "intent": "a", "why": "Needed for the task."},
                    {"id": "S1", "intent": "b", "why": "Needed for the task."},
                ]
            )
        )


def test_unknown_step_dependency_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown or itself: S9"):
        Plan.model_validate(
            plan(steps=[{"id": "S1", "intent": "a", "why": "Needed for the task.", "depends_on": ["S9"]}])
        )


def test_self_dependency_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown or itself: S1"):
        Plan.model_validate(
            plan(steps=[{"id": "S1", "intent": "a", "why": "Needed for the task.", "depends_on": ["S1"]}])
        )


def test_circular_dependencies_rejected() -> None:
    steps = [
        {"id": "S1", "intent": "a", "why": "Needed for the task.", "depends_on": ["S3"]},
        {"id": "S2", "intent": "b", "why": "Needed for the task.", "depends_on": ["S1"]},
        {"id": "S3", "intent": "c", "why": "Needed for the task.", "depends_on": ["S2"]},
    ]
    with pytest.raises(ValidationError, match="cycle"):
        Plan.model_validate(plan(steps=steps))


def test_valid_dependency_graph_accepted() -> None:
    steps = [
        {"id": "S1", "intent": "OCR", "why": "Needed for the task.", "tool": "ocr_document"},
        {"id": "S2", "intent": "Translate to English", "why": "Needed for the task.", "depends_on": ["S1"]},
        {"id": "S3", "intent": "Summarise", "why": "Needed for the task.", "depends_on": ["S2"]},
        {"id": "S4", "intent": "Speak", "why": "Needed for the task.", "depends_on": ["S1", "S3"]},
    ]
    result = Plan.model_validate(plan(steps=steps))
    assert [step.id for step in result.steps] == ["S1", "S2", "S3", "S4"]


def test_feasible_plan_needs_deliverables() -> None:
    with pytest.raises(ValidationError, match="at least one deliverable"):
        Plan.model_validate(plan(deliverables=[]))


def test_infeasible_plan_with_reason_accepted() -> None:
    result = Plan.model_validate(
        plan(
            deliverables=[],
            feasibility={
                "status": "infeasible",
                "reason_code": "unsupported_language",
                "explanation": "French is not supported by Bodhan.",
            },
        )
    )
    assert result.feasibility.reason_code == "unsupported_language"


@pytest.mark.parametrize(
    "feasibility",
    [
        {"status": "infeasible"},
        {"status": "infeasible", "reason_code": "missing_input"},
        {"status": "infeasible", "explanation": "No files."},
    ],
)
def test_infeasible_plan_needs_reason_and_explanation(feasibility: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="reason_code and explanation"):
        Plan.model_validate(plan(feasibility=feasibility))


def test_ambiguity_is_not_an_infeasible_reason() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(
            plan(feasibility={"status": "infeasible", "reason_code": "unclear_task", "explanation": "Vague."})
        )


def test_feasible_plan_drops_reason_code() -> None:
    result = Plan.model_validate(plan(feasibility={"status": "feasible", "reason_code": "beyond_tools"}))
    assert result.feasibility.reason_code is None


def test_deliverable_lookup() -> None:
    result = Plan.model_validate(plan(deliverables=[deliverable(), deliverable(id="D2", kind="text")]))
    found = result.deliverable("D2")
    assert found is not None and found.kind == "text"
    assert result.deliverable("D9") is None


def step(**overrides: Any) -> dict[str, Any]:
    return {"id": "S1", "intent": "Read the pages", "why": "All PDFs go through Bodhan OCR.",
            "tool": "ocr_document", **overrides}  # fmt: skip


def test_reasoning_fields_are_kept() -> None:
    parsed = Plan.model_validate(
        plan(
            input_notes=["8-page PDF; language unknown until it is read."],
            steps=[
                step(),
                step(
                    id="S2",
                    intent="Translate to English",
                    why="I need English to summarise.",
                    tool="translate_text",
                    condition="only if the text is not English",
                    depends_on=["S1"],
                ),
            ],  # fmt: skip
            risks=[{"risk": "Poor scan quality", "mitigation": "Report low-confidence pages."}],
        )
    )
    assert parsed.goal
    assert parsed.input_notes == ["8-page PDF; language unknown until it is read."]
    assert parsed.steps[0].why == "All PDFs go through Bodhan OCR." and parsed.steps[0].condition is None
    assert parsed.steps[1].condition == "only if the text is not English"
    assert (parsed.risks[0].risk, parsed.risks[0].mitigation) == ("Poor scan quality", "Report low-confidence pages.")


def test_reasoning_fields_default_empty() -> None:
    parsed = Plan.model_validate(plan())
    assert (parsed.input_notes, parsed.risks) == ([], [])


@pytest.mark.parametrize("field", ["goal", "task_en"])
def test_goal_and_task_are_required(field: str) -> None:
    data = plan()
    data.pop(field)
    with pytest.raises(ValidationError):
        Plan.model_validate(data)


@pytest.mark.parametrize("why", [None, "", "   "])
def test_step_needs_a_why(why: str | None) -> None:
    item = step()
    if why is None:
        item.pop("why")
    else:
        item["why"] = why
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(steps=[item]))


@pytest.mark.parametrize("risk", [{"risk": "x"}, {"mitigation": "y"}, {"risk": " ", "mitigation": "y"}])
def test_risk_needs_both_parts(risk: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(risks=[risk]))


def test_old_interpretation_field_is_ignored() -> None:
    parsed = Plan.model_validate(plan(interpretation="old field"))
    assert "interpretation" not in parsed.model_dump()


def test_input_notes_and_assumptions_accept_a_string() -> None:
    result = Plan.model_validate(plan(input_notes="The audio language is unknown.", assumptions="  "))
    assert (result.input_notes, result.assumptions) == (["The audio language is unknown."], [])


@pytest.mark.parametrize(("kind", "expected"), [("audio", "synthesize_speech"), ("subtitles", "make_subtitles")])
def test_single_producer_kinds_default_produced_by(kind: str, expected: str) -> None:
    assert Plan.model_validate(plan(deliverables=[deliverable(kind=kind)])).deliverables[0].produced_by == expected


def test_text_produced_by_is_optional_in_schema() -> None:
    assert Plan.model_validate(plan(deliverables=[deliverable(kind="text")])).deliverables[0].produced_by is None


@pytest.mark.parametrize(
    ("fields", "fragment"),
    [
        ({"kind": "audio", "produced_by": "translate_text"}, "takes synthesize_speech"),
        ({"kind": "text", "produced_by": "make_subtitles"}, "takes translate_text"),
        ({"kind": "answer", "produced_by": "write"}, "takes no produced_by"),
        ({"kind": "text", "language": "ta", "produced_by": "write"}, "write makes only English"),
    ],
)
def test_bad_produced_by_rejected(fields: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        Plan.model_validate(plan(deliverables=[deliverable(**fields)]))


def test_english_write_deliverable_accepted() -> None:
    item = Plan.model_validate(plan(deliverables=[deliverable(kind="text", language="en", produced_by="write")]))
    assert item.deliverables[0].produced_by == "write"


@pytest.mark.parametrize(
    ("fields", "kind", "path", "expected"),
    [
        ({"kind": "text"}, "markdown", "/w/a.md", None),
        ({"kind": "markdown"}, "text", "/w/a.txt", None),
        ({"kind": "text"}, "segments", "/w/a.json", "kind"),
        ({"kind": "text"}, "text", "/w/a.srt", "format"),
        ({"kind": "audio", "format": "wav"}, "audio", "/w/a.mp3", "format"),
        ({"kind": "subtitles"}, "text", "/w/a.txt", "kind"),
    ],
)
def test_file_problem(fields: dict[str, Any], kind: str, path: str, expected: str | None) -> None:
    item = Plan.model_validate(plan(deliverables=[deliverable(**fields)])).deliverables[0]
    problem = item.file_problem(kind, path)
    assert (problem and problem[0]) == expected


def test_producer_problem_rejects_only_llm_files_for_bodhan_deliverables() -> None:
    bodhan = Plan.model_validate(plan(deliverables=[deliverable(kind="text", produced_by="transcribe_audio")]))
    item = bodhan.deliverables[0]
    assert item.producer_problem("transcribe_audio") is None
    assert item.producer_problem("translate_text") is None
    assert "written by the LLM" in (item.producer_problem("write") or "")
    composed = Plan.model_validate(plan(deliverables=[deliverable(kind="text", language="en", produced_by="write")]))
    assert composed.deliverables[0].producer_problem("write") is None
    assert composed.deliverables[0].producer_problem("translate_text") is None
