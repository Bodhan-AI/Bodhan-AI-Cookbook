from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.plan import Plan
from bodhan_agent.tools.base import LocalToolError, ToolContext, file_digest, register, write_new
from bodhan_agent.tools.finish import FinishTool

TAMIL = "தமிழ் ஆவணம்."

PLAN = Plan.model_validate(
    {
        "task_en": "Translate the notes to Tamil, subtitle the talk and summarise it.",
        "goal": "Three outputs.",
        "deliverables": [
            {"id": "D1", "kind": "markdown", "language": "ta", "description": "Tamil notes"},
            {"id": "D2", "kind": "answer", "language": "en", "description": "English summary"},
            {"id": "D3", "kind": "subtitles", "language": "en", "description": "English subtitles"},
            {"id": "D4", "kind": "audio", "language": "ta", "format": "wav", "description": "Tamil speech"},
        ],
        "feasibility": {"status": "feasible"},
    }
)


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    settings.work_dir.mkdir(parents=True)
    return ToolContext(settings=settings, ledger=Ledger(), plan=PLAN)


def add(
    ctx: ToolContext,
    name: str,
    kind: str,
    producer: str = "bodhan",
    status: str = "ok",
    data: bytes = b"content",
    **facts: Any,
) -> Artifact:
    if producer == "bodhan":
        facts.setdefault("tool", "translate_text")
    artifact = register(ctx, write_new(ctx.settings.work_dir / name, data), kind, producer, **facts)
    ctx.ledger.set_status(artifact.id, status)
    return artifact


def user_file(ctx: ToolContext, name: str, kind: str, **facts: Any) -> Artifact:
    path = write_new(ctx.settings.work_dir / name, b"given")
    digest, size = file_digest(path)
    artifact = Artifact(
        id=ctx.ledger.next_id("input"), path=str(path), kind=kind, producer="user_input",
        sha256=digest, size_bytes=size, step_status="ok", **facts,
    )  # fmt: skip
    return ctx.ledger.add(artifact)


def tamil_doc(ctx: ToolContext, name: str = "notes.ta.md") -> Artifact:
    return add(ctx, name, "markdown", data=TAMIL.encode(), language="ta", indic_text=True)


def english_srt(ctx: ToolContext, name: str = "talk.en.srt") -> Artifact:
    return add(ctx, name, "subtitles", language="en", tool="make_subtitles")


async def finish(ctx: ToolContext, **arguments: Any) -> Any:
    arguments.setdefault("answer_en", "All done.")
    return await FinishTool().execute(arguments, ctx)


async def rejected(ctx: ToolContext, **arguments: Any) -> LocalToolError:
    with pytest.raises(LocalToolError) as error:
        await finish(ctx, **arguments)
    assert error.value.code == "finish_rejected"
    assert error.value.message.startswith("The finish proposal has problems: ")
    assert error.value.hint
    return error.value


def test_definition_is_named_finish_and_requires_answer() -> None:
    definition = FinishTool().definition()
    assert definition["function"]["name"] == "finish"
    assert definition["function"]["parameters"]["required"] == ["reason", "answer_en"]


async def test_accepts_valid_proposal(ctx: ToolContext) -> None:
    doc = tamil_doc(ctx)
    srt = english_srt(ctx)
    audio = add(ctx, "speech.ta.wav", "audio", language="ta", tool="synthesize_speech")
    result = await finish(
        ctx,
        assumptions=["The talk is in Hindi."],
        deliverables=[
            {"id": "D1", "path": doc.path},
            {"id": "D2"},
            {"id": "D3", "path": srt.path},
            {"id": "D4", "path": audio.path},
        ],
    )
    assert result.data == {
        "accepted": True,
        "deliverables": [
            {"id": "D1", "artifact": doc.id, "path": doc.path},
            {"id": "D2"},
            {"id": "D3", "artifact": srt.id, "path": srt.path},
            {"id": "D4", "artifact": audio.id, "path": audio.path},
        ],
        "missing": [],
        "assumptions": 1,
        "next": "Final verification by code; no further tool calls are needed.",
    }
    assert result.artifact_ids == [doc.id, srt.id, audio.id]


async def test_omitted_deliverables_are_listed_as_missing(ctx: ToolContext) -> None:
    doc = tamil_doc(ctx)
    result = await finish(ctx, deliverables=[{"id": "D1", "path": doc.path}])
    assert result.data["accepted"] is True
    assert result.data["missing"] == ["D2", "D3", "D4"]


async def test_no_deliverables_lists_all_as_missing(ctx: ToolContext) -> None:
    result = await finish(ctx)
    assert result.data["deliverables"] == []
    assert result.data["missing"] == ["D1", "D2", "D3", "D4"]
    assert result.artifact_ids == []


async def test_answer_deliverable_without_path_is_accepted(ctx: ToolContext) -> None:
    result = await finish(ctx, deliverables=[{"id": "D2"}])
    assert result.data["deliverables"] == [{"id": "D2"}]


async def test_answer_deliverable_with_path_is_rejected(ctx: ToolContext) -> None:
    doc = tamil_doc(ctx)
    error = await rejected(ctx, deliverables=[{"id": "D2", "path": doc.path}])
    assert error.details["problems"] == ["D2 is an answer in result.md; give no path for it."]


async def test_file_deliverable_without_path_is_rejected(ctx: ToolContext) -> None:
    error = await rejected(ctx, deliverables=[{"id": "D1"}])
    assert error.details["problems"] == [
        "D1 needs the path of the file that fulfils it. "
        "If it could not be made, leave it out and explain why in answer_en."
    ]


async def test_unknown_deliverable_id(ctx: ToolContext) -> None:
    doc = tamil_doc(ctx)
    error = await rejected(ctx, deliverables=[{"id": "D9", "path": doc.path}])
    assert error.details["problems"] == ["D9 is not a planned deliverable (planned: D1, D2, D3, D4)."]


async def test_wrong_kind(ctx: ToolContext) -> None:
    doc = tamil_doc(ctx)
    error = await rejected(ctx, deliverables=[{"id": "D3", "path": doc.path}])
    assert "D3 needs a subtitles file" in error.details["problems"][0]


async def test_wrong_extension(ctx: ToolContext) -> None:
    audio = add(ctx, "speech.ta.mp3", "audio", language="ta", tool="synthesize_speech")
    error = await rejected(ctx, deliverables=[{"id": "D4", "path": audio.path}])
    assert error.details["problems"] == [
        f"D4 needs a .wav file, not speech.ta.mp3 ({audio.id}). Call synthesize_speech with format 'wav'."
    ]


async def test_known_language_mismatch(ctx: ToolContext) -> None:
    english = add(ctx, "notes.en.md", "markdown", language="en")
    error = await rejected(ctx, deliverables=[{"id": "D1", "path": english.path}])
    assert error.details["problems"] == [f"D1 must be in 'ta', but {english.id} is in 'en'."]


async def test_language_matches_on_base_code(ctx: ToolContext) -> None:
    srt = add(ctx, "talk.srt", "subtitles", language="en-IN", tool="make_subtitles")
    result = await finish(ctx, deliverables=[{"id": "D3", "path": srt.path}])
    assert result.data["deliverables"][0]["artifact"] == srt.id


async def test_unknown_artifact_language_is_left_to_verification(ctx: ToolContext) -> None:
    doc = add(ctx, "notes.md", "markdown", data=TAMIL.encode(), indic_text=True)
    result = await finish(ctx, deliverables=[{"id": "D1", "path": doc.path}])
    assert result.data["deliverables"] == [{"id": "D1", "artifact": doc.id, "path": doc.path}]


async def test_llm_written_indic_deliverable_is_rejected(ctx: ToolContext) -> None:
    fake = add(ctx, "fake.ta.md", "markdown", producer="llm_english", language="ta")
    error = await rejected(ctx, deliverables=[{"id": "D1", "path": fake.path}])
    assert "written by the LLM" in error.details["problems"][0]


async def test_user_input_is_not_a_deliverable(ctx: ToolContext) -> None:
    given = user_file(ctx, "given.en.srt", "subtitles", language="en")
    error = await rejected(ctx, deliverables=[{"id": "D3", "path": given.path}])
    assert "one of the user's inputs" in error.details["problems"][0]


async def test_code_composed_from_bodhan_is_accepted(ctx: ToolContext) -> None:
    segments = add(ctx, "talk.segments.json", "segments", tool="transcribe_audio")
    srt = add(
        ctx, "talk.ta.srt", "subtitles", producer="code_composed", language="ta", indic_text=True,
        sources=[segments.id], tool="make_subtitles",
    )  # fmt: skip
    plan = PLAN.model_copy(deep=True)
    plan.deliverables[2].language = "ta"
    result = await finish(dataclasses.replace(ctx, plan=plan), deliverables=[{"id": "D3", "path": srt.path}])
    assert result.artifact_ids == [srt.id]


async def test_stray_path_is_rejected(ctx: ToolContext) -> None:
    error = await rejected(ctx, deliverables=[{"id": "D1", "path": "/etc/hosts"}])
    assert error.details["problems"] == ["D1: '/etc/hosts' is not a file of this run."]


async def test_relative_path_is_rejected(ctx: ToolContext) -> None:
    error = await rejected(ctx, deliverables=[{"id": "D1", "path": "notes.ta.md"}])
    assert "not an absolute path" in error.details["problems"][0]


async def test_failed_artifact_is_rejected(ctx: ToolContext) -> None:
    doc = add(ctx, "bad.ta.md", "markdown", status="failed", language="ta", indic_text=True)
    error = await rejected(ctx, deliverables=[{"id": "D1", "path": doc.path}])
    assert "status 'failed'" in error.details["problems"][0]


async def test_changed_file_is_rejected(ctx: ToolContext) -> None:
    doc = tamil_doc(ctx)
    Path(doc.path).write_text("tampered", encoding="utf-8")
    error = await rejected(ctx, deliverables=[{"id": "D1", "path": doc.path}])
    assert "changed on disk" in error.details["problems"][0]


async def test_deleted_file_is_rejected(ctx: ToolContext) -> None:
    doc = tamil_doc(ctx)
    Path(doc.path).unlink()
    error = await rejected(ctx, deliverables=[{"id": "D1", "path": doc.path}])
    assert "no longer exists" in error.details["problems"][0]


async def test_indic_answer_is_rejected(ctx: ToolContext) -> None:
    error = await rejected(ctx, answer_en="काम हो गया।")
    assert error.details["problems"][0].startswith("answer_en contains Indian-language script")


async def test_romanized_assumption_is_rejected(ctx: ToolContext) -> None:
    error = await rejected(ctx, assumptions=["Fine.", "sab theek hai aur bahut accha hai yaar"])
    assert len(error.details["problems"]) == 1
    assert error.details["problems"][0].startswith("assumptions looks like romanized")


async def test_all_problems_are_returned_together(ctx: ToolContext) -> None:
    english = add(ctx, "notes.en.md", "markdown", language="en")
    error = await rejected(
        ctx,
        answer_en="हो गया",
        deliverables=[{"id": "D1", "path": english.path}, {"id": "D2", "path": english.path}, {"id": "D9"}],
    )
    problems = error.details["problems"]
    assert len(problems) == 4
    assert problems[0].startswith("answer_en")
    assert problems[1].startswith("D1 must be in 'ta'")
    assert problems[2].startswith("D2 is an answer")
    assert problems[3].startswith("D9 is not a planned deliverable")
    assert " ".join(problems) in error.message


async def test_without_plan_only_files_and_text_are_checked(ctx: ToolContext) -> None:
    no_plan = dataclasses.replace(ctx, plan=None)
    doc = tamil_doc(ctx)
    result = await finish(no_plan, deliverables=[{"id": "D7", "path": doc.path}])
    assert result.data["deliverables"] == [{"id": "D7", "artifact": doc.id, "path": doc.path}]
    assert result.data["missing"] == []
    error = await rejected(no_plan, deliverables=[{"id": "D8"}])
    assert error.details["problems"] == [
        "D8 needs the path of the file that fulfils it. "
        "If it could not be made, leave it out and explain why in answer_en."
    ]


async def test_without_plan_llm_indic_file_still_rejected(ctx: ToolContext) -> None:
    fake = add(ctx, "fake.md", "markdown", producer="llm_english", language="ta")
    error = await rejected(dataclasses.replace(ctx, plan=None), deliverables=[{"id": "D1", "path": fake.path}])
    assert "written by the LLM" in error.details["problems"][0]


async def test_without_plan_user_input_still_rejected(ctx: ToolContext) -> None:
    given = user_file(ctx, "given.en.srt", "subtitles", language="en")
    error = await rejected(dataclasses.replace(ctx, plan=None), deliverables=[{"id": "D1", "path": given.path}])
    assert "one of the user's inputs" in error.details["problems"][0]


@pytest.mark.parametrize(
    "arguments",
    [
        {"answer_en": "   "},
        {"answer_en": "ok", "deliverables": [{"id": "D1"}, {"id": "D1"}]},
        {"answer_en": "ok", "deliverables": [{"id": "X1"}]},
    ],
)
async def test_schema_errors_are_invalid_input(ctx: ToolContext, arguments: dict[str, Any]) -> None:
    with pytest.raises(LocalToolError) as error:
        await FinishTool().execute(arguments, ctx)
    assert error.value.code == "invalid_input"


async def test_does_not_change_ledger(ctx: ToolContext) -> None:
    doc = tamil_doc(ctx)
    before = ctx.ledger.model_dump()
    await finish(ctx, deliverables=[{"id": "D1", "path": doc.path}])
    assert ctx.ledger.model_dump() == before


async def test_llm_placeholder_for_bodhan_translation_is_rejected(ctx: ToolContext) -> None:
    plan = PLAN.model_copy(deep=True)
    plan.deliverables[0] = plan.deliverables[0].model_copy(update={"language": "en", "produced_by": "translate_text"})
    fake = add(ctx, "talk.en.md", "markdown", producer="llm_english", language="en", tool="write")
    error = await rejected(dataclasses.replace(ctx, plan=plan), deliverables=[{"id": "D1", "path": fake.path}])
    assert error.details["problems"] == [
        f"D1 must be Bodhan's translate_text output, but it was written by the LLM with write ({fake.id}); "
        "deliver Bodhan's file as is."
    ]


async def test_bodhan_txt_fulfils_markdown_deliverable(ctx: ToolContext) -> None:
    plan = PLAN.model_copy(deep=True)
    plan.deliverables[0] = plan.deliverables[0].model_copy(update={"produced_by": "translate_text"})
    text = add(ctx, "notes.ta.txt", "text", data=TAMIL.encode(), language="ta", indic_text=True)
    result = await finish(dataclasses.replace(ctx, plan=plan), deliverables=[{"id": "D1", "path": text.path}])
    assert result.artifact_ids == [text.id]


async def test_other_bodhan_tool_fulfils_bodhan_deliverable(ctx: ToolContext) -> None:
    plan = PLAN.model_copy(deep=True)
    plan.deliverables[0] = plan.deliverables[0].model_copy(update={"language": "en", "produced_by": "transcribe_audio"})
    text = add(ctx, "talk.en.txt", "text", language="en", tool="translate_text")
    result = await finish(dataclasses.replace(ctx, plan=plan), deliverables=[{"id": "D1", "path": text.path}])
    assert result.artifact_ids == [text.id]
