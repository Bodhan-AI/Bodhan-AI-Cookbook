from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.agent.observe import Observer
from bodhan_agent.core import guards
from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.tools import read as read_module
from bodhan_agent.tools.base import LocalToolError, ToolContext, register, write_new
from bodhan_agent.tools.read import ReadTool, english_version

TAMIL = "தமிழ் பாடம் இது. "
ENGLISH = "Plants make food from sunlight. "


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    settings = dataclasses.replace(RunSettings.create("t", [], str(tmp_path / "out")), read_max_chars=100)
    settings.work_dir.mkdir(parents=True)
    return ToolContext(settings=settings, ledger=Ledger())


def add(ctx: ToolContext, name: str, data: str | bytes, status: str = "ok", **fields: Any) -> Artifact:
    fields.setdefault("kind", "markdown")
    fields.setdefault("tool", "ocr_document")
    raw = data.encode("utf-8") if isinstance(data, str) else data
    artifact = register(ctx, write_new(ctx.settings.work_dir / name, raw), producer="bodhan", **fields)
    ctx.ledger.set_status(artifact.id, status)
    return artifact


async def read(ctx: ToolContext, **arguments: Any) -> dict[str, Any]:
    return (await ReadTool().execute(arguments, ctx)).data


async def read_error(ctx: ToolContext, **arguments: Any) -> LocalToolError:
    with pytest.raises(LocalToolError) as error:
        await ReadTool().execute(arguments, ctx)
    return error.value


def test_definition_is_named_read_and_has_reason() -> None:
    definition = ReadTool().definition()
    assert definition["function"]["name"] == "read"
    assert definition["function"]["parameters"]["required"] == ["reason", "path"]


async def test_reads_whole_short_file(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.en.md", "Short English text.")
    data = await read(ctx, path=artifact.path)
    assert data == {
        "artifact": artifact.id,
        "path": artifact.path,
        "offset": 0,
        "chars": 19,
        "total_chars": 19,
        "next_offset": None,
        "truncated": False,
        "text": "Short English text.",
    }


async def test_pages_through_long_file(ctx: ToolContext) -> None:
    text = ENGLISH * 8
    artifact = add(ctx, "long.en.md", text)
    first = await read(ctx, path=artifact.path)
    second = await read(ctx, path=artifact.path, offset=first["next_offset"])
    third = await read(ctx, path=artifact.path, offset=second["next_offset"])
    assert (first["chars"], first["next_offset"], first["truncated"]) == (100, 100, True)
    assert (second["offset"], second["next_offset"]) == (100, 200)
    assert (third["chars"], third["next_offset"], third["truncated"]) == (56, None, False)
    assert first["text"] + second["text"] + third["text"] == text


async def test_range_with_offset_and_max_chars(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.en.md", "0123456789" * 5)
    data = await read(ctx, path=artifact.path, offset=10, max_chars=15)
    assert (data["text"], data["next_offset"]) == ("012345678901234", 25)


async def test_run_limit_caps_requested_size(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.en.md", "x" * 500)
    assert (await read(ctx, path=artifact.path, max_chars=12_000))["chars"] == 100


async def test_offset_past_end(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.en.md", "abc")
    data = await read(ctx, path=artifact.path, offset=50)
    assert (data["text"], data["offset"], data["chars"], data["next_offset"]) == ("", 3, 0, None)
    assert "past the end" in data["note"]


async def test_bom_is_removed(ctx: ToolContext) -> None:
    artifact = add(ctx, "bom.txt", "﻿Hello", kind="text")
    assert (await read(ctx, path=artifact.path))["text"] == "Hello"


@pytest.mark.parametrize("kind", ["text", "markdown", "subtitles", "segments", "blocks"])
async def test_readable_kinds(ctx: ToolContext, kind: str) -> None:
    artifact = add(ctx, f"f.{kind}", "plain English", kind=kind)
    assert (await read(ctx, path=artifact.path))["text"] == "plain English"


@pytest.mark.parametrize("kind", ["audio", "video", "image", "pdf", "unknown"])
async def test_binary_kinds_refused(ctx: ToolContext, kind: str) -> None:
    artifact = add(ctx, f"f.{kind}", "x", kind=kind)
    error = await read_error(ctx, path=artifact.path)
    assert error.code == "unsupported_input" and "ocr_document" in error.hint


async def test_mostly_indic_file_refused_with_translate_hint(ctx: ToolContext) -> None:
    artifact = add(ctx, "ch.ta.md", TAMIL * 10, indic_text=True)
    error = await read_error(ctx, path=artifact.path)
    assert error.code == "indic_content"
    assert f"text_path={artifact.path}" in error.hint and "target_language='en'" in error.hint


async def test_indic_refusal_points_to_existing_english_translation(ctx: ToolContext) -> None:
    tamil = add(ctx, "ch.ta.md", TAMIL * 10, indic_text=True)
    english = add(ctx, "ch.en.md", ENGLISH, tool="translate_text", language="en-IN", sources=[tamil.id])
    error = await read_error(ctx, path=tamil.path)
    assert error.details["english_version"] == {"id": english.id, "path": english.path}
    assert english.id in error.hint


async def test_mislabelled_indic_file_still_refused(ctx: ToolContext) -> None:
    artifact = add(ctx, "hidden.md", TAMIL * 10)
    assert (await read_error(ctx, path=artifact.path)).code == "indic_content"


async def test_mixed_file_is_read_and_observer_hides_indic(ctx: ToolContext) -> None:
    text = "This chapter explains how models generalize. यह एक उदाहरण है। We now discuss overfitting."
    artifact = add(ctx, "mixed.md", text, indic_text=True)
    result = await ReadTool().execute({"path": artifact.path}, ctx)
    assert result.data["text"] == text
    observation = Observer(ctx.ledger, read_chars=ctx.settings.read_max_chars).local_result(
        "read", result.data, result.artifact_ids
    )
    shown = json.loads(observation.content)["preview"]["text"]
    assert "This chapter explains" in shown and not guards.contains_indic(shown)
    assert observation.redactions == 1


async def test_invalid_utf8_refused(ctx: ToolContext) -> None:
    artifact = add(ctx, "bad.txt", b"\xff\xfe\x00bad", kind="text")
    assert (await read_error(ctx, path=artifact.path)).code == "unsupported_encoding"


async def test_huge_file_refused(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read_module, "MAX_READ_FILE_BYTES", 10)
    artifact = add(ctx, "big.txt", "x" * 50, kind="text")
    error = await read_error(ctx, path=artifact.path)
    assert error.code == "unsupported_input" and "larger than" in error.message


async def test_unknown_and_relative_paths_refused(ctx: ToolContext) -> None:
    assert (await read_error(ctx, path="/etc/hostname")).code == "path_not_allowed"
    assert (await read_error(ctx, path="a.en.md")).code == "invalid_input"


async def test_unusable_file_refused(ctx: ToolContext) -> None:
    artifact = add(ctx, "failed.md", ENGLISH, status="failed")
    assert (await read_error(ctx, path=artifact.path)).code == "artifact_not_usable"


async def test_changed_file_refused(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.en.md", ENGLISH)
    Path(artifact.path).write_text("tampered")
    assert (await read_error(ctx, path=artifact.path)).code == "file_changed"


async def test_bad_arguments_refused(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.en.md", ENGLISH)
    assert (await read_error(ctx, path=artifact.path, offset=-1)).code == "invalid_input"
    assert (await read_error(ctx, path=artifact.path, max_chars=12_001)).code == "invalid_input"


async def test_result_lists_the_artifact(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.en.md", ENGLISH)
    result = await ReadTool().execute({"path": artifact.path}, ctx)
    assert result.artifact_ids == [artifact.id]


def test_english_version_rules(ctx: ToolContext) -> None:
    tamil = add(ctx, "ch.ta.md", TAMIL, indic_text=True)
    other = add(ctx, "other.ta.md", TAMIL, indic_text=True)
    assert english_version(ctx.ledger, tamil) is None
    add(ctx, "other.en.md", ENGLISH, tool="translate_text", language="en", sources=[other.id])
    add(ctx, "hindi.md", ENGLISH, tool="translate_text", language="hi", sources=[tamil.id])
    add(ctx, "failed.en.md", ENGLISH, status="failed", tool="translate_text", language="en", sources=[tamil.id])
    assert english_version(ctx.ledger, tamil) is None
    summary = add(ctx, "summary.ta.md", TAMIL, tool="translate_text", language="ta", sources=[tamil.id])
    deep = add(ctx, "summary.en.md", ENGLISH, tool="translate_text", language="en", sources=[summary.id])
    assert english_version(ctx.ledger, tamil).id == deep.id
    newest = add(ctx, "again.en.md", ENGLISH, tool="translate_text", language="en", sources=[tamil.id])
    assert english_version(ctx.ledger, tamil).id == newest.id
