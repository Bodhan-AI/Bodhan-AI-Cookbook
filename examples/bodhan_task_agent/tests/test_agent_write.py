from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.ledger import Ledger
from bodhan_agent.schemas.tools import WRITE_LIMIT
from bodhan_agent.tools.base import LocalToolError, ToolContext
from bodhan_agent.tools.read import ReadTool
from bodhan_agent.tools.write import DEFAULT_NAME, WriteTool

SUMMARY = "# Summary\n\nPlants make food from sunlight.\n"


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    settings.work_dir.mkdir(parents=True)
    settings.artifacts_dir.mkdir(parents=True)
    return ToolContext(settings=settings, ledger=Ledger(), seq=5)


async def write(ctx: ToolContext, **arguments: Any) -> dict[str, Any]:
    return (await WriteTool().execute(arguments, ctx)).data


async def write_error(ctx: ToolContext, **arguments: Any) -> LocalToolError:
    with pytest.raises(LocalToolError) as error:
        await WriteTool().execute(arguments, ctx)
    return error.value


def files(ctx: ToolContext) -> list[str]:
    roots = (ctx.settings.work_dir, ctx.settings.artifacts_dir)
    return sorted(str(path.relative_to(ctx.settings.out_dir)) for root in roots for path in root.rglob("*"))


def test_definition_is_named_write_and_requires_path_and_content() -> None:
    definition = WriteTool().definition()
    assert definition["function"]["name"] == "write"
    assert definition["function"]["parameters"]["required"] == ["reason", "path", "content"]


async def test_writes_markdown_into_work_and_registers_it(ctx: ToolContext) -> None:
    data = await write(ctx, path="summary.en.md", content=SUMMARY)
    path = ctx.settings.work_dir / "summary.en.md"
    assert data == {"artifact": "A1", "path": str(path.resolve()), "chars": len(SUMMARY), "language": "en"}
    assert path.read_text(encoding="utf-8") == SUMMARY
    artifact = ctx.ledger.get("A1")
    assert artifact is not None
    assert (artifact.kind, artifact.producer, artifact.step_status) == ("markdown", "llm_english", "ok")
    assert (artifact.language, artifact.language_source, artifact.script) == ("en", "requested", "Latin")
    assert (artifact.created_seq, artifact.untrusted, artifact.sources, artifact.tool) == (5, True, [], "write")
    assert artifact.sha256 == hashlib.sha256(SUMMARY.encode("utf-8")).hexdigest()
    assert artifact.size_bytes == len(SUMMARY.encode("utf-8"))


async def test_result_lists_the_new_artifact(ctx: ToolContext) -> None:
    result = await WriteTool().execute({"path": "a.md", "content": SUMMARY}, ctx)
    assert result.artifact_ids == ["A1"]


async def test_txt_files_are_text_kind(ctx: ToolContext) -> None:
    await write(ctx, path="notes.TXT", content="Plain notes.")
    artifact = ctx.ledger.get("A1")
    assert artifact is not None and artifact.kind == "text"


async def test_written_file_can_be_read_back(ctx: ToolContext) -> None:
    data = await write(ctx, path="summary.en.md", content=SUMMARY)
    read = await ReadTool().execute({"path": data["path"]}, ctx)
    assert read.data["text"] == SUMMARY
    assert read.data["artifact"] == "A1"


async def test_writes_into_artifacts_with_absolute_path(ctx: ToolContext) -> None:
    target = ctx.settings.artifacts_dir / "answer.txt"
    data = await write(ctx, path=str(target), content="The answer is 42.")
    assert data["path"] == str(target.resolve())
    assert target.read_text(encoding="utf-8") == "The answer is 42."


async def test_writes_into_work_subfolder(ctx: ToolContext) -> None:
    (ctx.settings.work_dir / "drafts").mkdir()
    await write(ctx, path="drafts/one.md", content=SUMMARY)
    assert (ctx.settings.work_dir / "drafts" / "one.md").exists()


async def test_blank_path_uses_default_name(ctx: ToolContext) -> None:
    data = await write(ctx, path="   ", content=SUMMARY)
    assert data["path"] == str((ctx.settings.work_dir / DEFAULT_NAME).resolve())


async def test_successive_writes_get_new_ids(ctx: ToolContext) -> None:
    first = await write(ctx, path="a.md", content=SUMMARY)
    second = await write(ctx, path="b.md", content="Second note.")
    assert (first["artifact"], second["artifact"]) == ("A1", "A2")


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("सारांश: पौधे भोजन बनाते हैं।", "indic_script"),
        ("Summary: plants are green. பச்சை", "indic_script"),
        ("aap kaise hain, main theek hoon, bahut accha hai", "romanized_indic"),
    ],
)
async def test_rejects_non_english_content_without_writing(ctx: ToolContext, content: str, code: str) -> None:
    error = await write_error(ctx, path="bad.md", content=content)
    assert error.code == code
    assert error.message.startswith("content ")
    assert files(ctx) == []
    assert ctx.ledger.artifacts == {}


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("subs.srt", "invalid_input"),
        ("data.json", "invalid_input"),
        ("noext", "invalid_input"),
        ("../escape.md", "path_not_allowed"),
        ("/etc/passwd.md", "path_not_allowed"),
        (".hidden.md", "path_not_allowed"),
        ("सारांश.md", "indic_text"),
    ],
)
async def test_rejects_bad_paths_without_writing(ctx: ToolContext, path: str, code: str) -> None:
    error = await write_error(ctx, path=path, content=SUMMARY)
    assert error.code == code
    assert files(ctx) == []
    assert ctx.ledger.artifacts == {}


async def test_never_overwrites_existing_file(ctx: ToolContext) -> None:
    await write(ctx, path="summary.md", content=SUMMARY)
    error = await write_error(ctx, path="summary.md", content="Replacement.")
    assert error.code == "file_exists"
    assert (ctx.settings.work_dir / "summary.md").read_text(encoding="utf-8") == SUMMARY
    assert list(ctx.ledger.artifacts) == ["A1"]


async def test_never_overwrites_unregistered_file(ctx: ToolContext) -> None:
    existing = ctx.settings.work_dir / "user.md"
    existing.write_text("untouched", encoding="utf-8")
    error = await write_error(ctx, path="user.md", content=SUMMARY)
    assert error.code == "file_exists"
    assert existing.read_text(encoding="utf-8") == "untouched"


@pytest.mark.parametrize("content", ["", "   \n\t"])
async def test_rejects_blank_content(ctx: ToolContext, content: str) -> None:
    error = await write_error(ctx, path="a.md", content=content)
    assert error.code == "invalid_input"
    assert files(ctx) == []


async def test_rejects_content_over_limit(ctx: ToolContext) -> None:
    error = await write_error(ctx, path="a.md", content="a" * (WRITE_LIMIT + 1))
    assert error.code == "invalid_input"
    assert files(ctx) == []


async def test_unicode_punctuation_is_allowed(ctx: ToolContext) -> None:
    content = "Café — “quoted” text… 25°C"
    data = await write(ctx, path="a.md", content=content)
    assert data["chars"] == len(content)
    assert (ctx.settings.work_dir / "a.md").read_text(encoding="utf-8") == content


TRANSLATION = "Every significant change in life begins with a small step. Start today and trust yourself."


def bodhan_text(ctx: ToolContext, text: str = TRANSLATION, **facts: Any) -> Any:
    from bodhan_agent.tools.base import register, write_new

    artifact = register(ctx, write_new(ctx.settings.work_dir / "talk.en.txt", text.encode()), "text", "bodhan",
                        tool="translate_text", language="en", **facts)  # fmt: skip
    ctx.ledger.set_status(artifact.id, "ok")
    return artifact


async def test_copy_of_bodhan_text_is_refused(ctx: ToolContext) -> None:
    source = bodhan_text(ctx)
    error = await write_error(ctx, content=f"  {TRANSLATION}\n", path="copy.en.txt")
    assert error.code == "duplicate_text"
    assert error.hint == f"Use {source.id} at {source.path} directly instead of writing it again."
    assert not (ctx.settings.work_dir / "copy.en.txt").exists()


async def test_near_copy_is_refused(ctx: ToolContext) -> None:
    bodhan_text(ctx)
    error = await write_error(ctx, content=TRANSLATION.replace("Start", "Begin"), path="copy.txt")
    assert error.code == "duplicate_text"


async def test_summary_of_bodhan_text_is_allowed(ctx: ToolContext) -> None:
    bodhan_text(ctx)
    data = await write(ctx, content="Change starts small; act today.", path="summary.en.md")
    assert data["chars"] == len("Change starts small; act today.")


async def test_copy_of_failed_bodhan_text_is_allowed(ctx: ToolContext) -> None:
    source = bodhan_text(ctx)
    ctx.ledger.set_status(source.id, "failed")
    assert (await write(ctx, content=TRANSLATION, path="copy.txt"))["language"] == "en"
