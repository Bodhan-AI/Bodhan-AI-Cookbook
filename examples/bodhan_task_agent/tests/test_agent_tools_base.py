from __future__ import annotations

import asyncio
import dataclasses
import json
import typing
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.tools import (
    DeliverableRef,
    DetectLanguageInput,
    FinishInput,
    LocalResult,
    MakeSubtitlesInput,
    ReadInput,
    WriteInput,
)
from bodhan_agent.tools.base import (
    LocalTool,
    LocalToolError,
    ToolContext,
    file_digest,
    function_tool,
    inline_schema,
    known_file,
    output_file,
    register,
    split_meta,
    with_meta,
    write_new,
)


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    settings.work_dir.mkdir(parents=True)
    settings.artifacts_dir.mkdir(parents=True)
    return ToolContext(settings=settings, ledger=Ledger(), seq=7)


def registered(ctx: ToolContext, name: str = "a.md", status: str = "ok") -> Artifact:
    path = write_new(ctx.settings.work_dir / name, b"hello")
    artifact = register(ctx, path, "markdown", "llm_english")
    ctx.ledger.set_status(artifact.id, status)
    return artifact


class EchoTool(LocalTool):
    name = "read"
    description = "Read a file."
    Input = ReadInput

    async def run(self, params: Any, ctx: ToolContext) -> LocalResult:
        return LocalResult(data={"path": params.path, "max_chars": params.max_chars})


def test_read_input_defaults_and_limits() -> None:
    params = ReadInput(path="/w/a.md")
    assert (params.offset, params.max_chars) == (0, 12_000)
    for fields in ({"max_chars": 12_001}, {"max_chars": 0}, {"offset": -1}, {"extra": 1}, {"path": ""}):
        with pytest.raises(ValidationError):
            ReadInput(**{"path": "/w/a.md", **fields})


def test_write_input_keeps_formatting_and_rejects_blank() -> None:
    assert WriteInput(path="a.md", content="  # Title\n\n  indented").content == "  # Title\n\n  indented"
    for content in ("", " \n\t "):
        with pytest.raises(ValidationError):
            WriteInput(path="a.md", content=content)
    with pytest.raises(ValidationError):
        WriteInput(path="a.md", content="x" * 50_001)


def test_detect_language_input() -> None:
    assert DetectLanguageInput(path="/w/a.txt").path == "/w/a.txt"


def test_subtitles_input() -> None:
    from bodhan_mcp.core.languages import TranslateTarget

    assert "en" in typing.get_args(TranslateTarget)
    assert MakeSubtitlesInput(segments_path="/w/s.json", target_language="en", output_path=" ").output_path is None
    assert MakeSubtitlesInput(segments_path="/w/s.json", target_language="hi", output_path="x.SRT").output_path
    for fields in ({"target_language": "fr"}, {"output_path": "x.vtt"}):
        with pytest.raises(ValidationError):
            MakeSubtitlesInput(**{"segments_path": "/w/s.json", "target_language": "en", **fields})


def test_finish_input() -> None:
    params = FinishInput(answer_en="Done.", deliverables=[{"id": "D1", "path": "/a/x.mp3"}, {"id": " D2 "}])
    assert params.deliverables[1] == DeliverableRef(id="D2", path=None)
    assert params.assumptions == []
    for fields in (
        {"answer_en": "   "},
        {"deliverables": [{"id": "D1"}, {"id": "D1"}]},
        {"deliverables": [{"id": "d1"}]},
        {"deliverables": [{"id": "D0"}]},
        {"deliverables": [{"id": "D1", "extra": 1}]},
    ):
        with pytest.raises(ValidationError):
            FinishInput(**{"answer_en": "Done.", **fields})


def test_local_result_defaults() -> None:
    assert LocalResult().model_dump() == {"data": {}, "artifact_ids": [], "usage": None}


def test_inline_schema_removes_refs_and_titles() -> None:
    schema = inline_schema(FinishInput.model_json_schema())
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and '"title"' not in text
    assert schema["properties"]["deliverables"]["items"]["properties"]["id"]["type"] == "string"


def test_with_meta_puts_reason_first_and_required() -> None:
    schema = with_meta({"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
    assert list(schema["properties"]) == ["reason", "path", "plan_step"]
    assert schema["required"] == ["reason", "path"]
    assert schema["type"] == "object"


def test_with_meta_replaces_existing_meta_fields() -> None:
    schema = with_meta(
        {"properties": {"reason": {"type": "integer"}, "x": {"type": "string"}}, "required": ["reason", "x"]}
    )
    assert schema["properties"]["reason"]["type"] == "string"
    assert schema["required"] == ["reason", "x"]


def test_with_meta_keeps_additional_properties_rule() -> None:
    schema = with_meta(ReadInput.model_json_schema())
    assert schema["additionalProperties"] is False


def test_function_tool_shape() -> None:
    tool = function_tool("read", "Read a file.", ReadInput.model_json_schema())
    assert tool["type"] == "function"
    assert (tool["function"]["name"], tool["function"]["description"]) == ("read", "Read a file.")
    assert tool["function"]["parameters"]["required"][0] == "reason"


def test_split_meta() -> None:
    assert split_meta({"reason": " read it ", "plan_step": "S2", "path": "/a"}) == ({"path": "/a"}, "read it", "S2")
    assert split_meta({"reason": 5, "plan_step": "  ", "path": "/a"}) == ({"path": "/a"}, None, None)
    original = {"reason": "x", "path": "/a"}
    split_meta(original)
    assert original == {"reason": "x", "path": "/a"}


def test_context_is_frozen(ctx: ToolContext) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.seq = 8  # type: ignore[misc]
    copy = dataclasses.replace(ctx, seq=8)
    assert (copy.seq, ctx.seq) == (8, 7)
    assert copy.ledger is ctx.ledger


def test_write_new_and_register(ctx: ToolContext) -> None:
    path = write_new(ctx.settings.work_dir / "sub" / "a.md", b"hello")
    artifact = register(ctx, path, "markdown", "llm_english", sources=[])
    assert path.read_bytes() == b"hello"
    assert (artifact.id, artifact.created_seq, artifact.size_bytes, artifact.step_status) == ("A1", 7, 5, "pending")
    assert artifact.sha256 == file_digest(path)[0]
    assert not list(path.parent.glob(".tmp-*"))


def test_write_new_never_overwrites(ctx: ToolContext) -> None:
    path = write_new(ctx.settings.work_dir / "a.md", b"first")
    with pytest.raises(LocalToolError) as error:
        write_new(path, b"second")
    assert error.value.code == "file_exists"
    assert path.read_bytes() == b"first"


async def test_parallel_writes_to_same_path_one_wins(ctx: ToolContext) -> None:
    target = ctx.settings.work_dir / "race.md"

    async def attempt(data: bytes) -> str:
        try:
            await asyncio.to_thread(write_new, target, data)
            return "ok"
        except LocalToolError as error:
            return error.code

    results = await asyncio.gather(*(attempt(f"v{index}".encode()) for index in range(6)))
    assert results.count("ok") == 1 and results.count("file_exists") == 5


def test_known_file_accepts_usable_registered_file(ctx: ToolContext) -> None:
    artifact = registered(ctx)
    assert known_file(ctx, f"  {artifact.path} ").id == artifact.id


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_known_file_rejects_unusable(ctx: ToolContext, status: str) -> None:
    artifact = registered(ctx, status=status)
    with pytest.raises(LocalToolError) as error:
        known_file(ctx, artifact.path)
    assert error.value.code == "artifact_not_usable"


def test_known_file_accepts_warning_status(ctx: ToolContext) -> None:
    artifact = registered(ctx, status="warning")
    assert known_file(ctx, artifact.path).id == artifact.id


@pytest.mark.parametrize(("raw", "code"), [("a.md", "invalid_input"), ("/etc/hostname", "path_not_allowed")])
def test_known_file_rejects_unknown_paths(ctx: ToolContext, raw: str, code: str) -> None:
    with pytest.raises(LocalToolError) as error:
        known_file(ctx, raw)
    assert error.value.code == code


def test_known_file_detects_deleted_and_changed_files(ctx: ToolContext) -> None:
    changed = registered(ctx, "changed.md")
    Path(changed.path).write_text("tampered")
    with pytest.raises(LocalToolError) as error:
        known_file(ctx, changed.path)
    assert error.value.code == "file_changed"
    deleted = registered(ctx, "deleted.md")
    Path(deleted.path).unlink()
    with pytest.raises(LocalToolError) as error:
        known_file(ctx, deleted.path)
    assert error.value.code == "file_not_found"


def test_output_file_locations(ctx: ToolContext) -> None:
    out = ctx.settings.out_dir
    assert output_file(ctx, "summary.en.md", "x.md", (".md",)) == out / "work" / "summary.en.md"
    assert output_file(ctx, None, "default.md", (".md",)) == out / "work" / "default.md"
    assert output_file(ctx, "  ", "default.md", (".md",)) == out / "work" / "default.md"
    assert output_file(ctx, "notes/a.md", "x.md", (".md",)) == out / "work" / "notes" / "a.md"
    assert output_file(ctx, str(out / "artifacts" / "final.md"), "x.md", (".md",)) == out / "artifacts" / "final.md"
    assert output_file(ctx, "any.bin", "x.bin", ()) == out / "work" / "any.bin"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("../escape.md", "path_not_allowed"),
        ("/tmp/x.md", "path_not_allowed"),
        (".hidden.md", "path_not_allowed"),
        ("notes/.secret/a.md", "path_not_allowed"),
        ("नया.md", "indic_text"),
        ("summary.pdf", "invalid_input"),
    ],
)
def test_output_file_rejections(ctx: ToolContext, raw: str, code: str) -> None:
    with pytest.raises(LocalToolError) as error:
        output_file(ctx, raw, "x.md", (".md", ".txt"))
    assert error.value.code == code


def test_output_file_rejects_existing_and_registered(ctx: ToolContext) -> None:
    (ctx.settings.work_dir / "exists.md").write_text("x")
    with pytest.raises(LocalToolError) as error:
        output_file(ctx, "exists.md", "x.md", (".md",))
    assert error.value.code == "file_exists"


def test_output_file_rejects_symlink_escape(ctx: ToolContext, tmp_path: Path) -> None:
    (ctx.settings.work_dir / "link").symlink_to(tmp_path)
    with pytest.raises(LocalToolError) as error:
        output_file(ctx, "link/x.md", "x.md", (".md",))
    assert error.value.code == "path_not_allowed"


async def test_local_tool_execute_and_definition(ctx: ToolContext) -> None:
    tool = EchoTool()
    result = await tool.execute({"path": "/w/a.md"}, ctx)
    assert result.data == {"path": "/w/a.md", "max_chars": 12_000}
    definition = tool.definition()
    assert definition["function"]["name"] == "read"
    assert list(definition["function"]["parameters"]["properties"])[0] == "reason"


async def test_local_tool_parse_error_lists_fields(ctx: ToolContext) -> None:
    with pytest.raises(LocalToolError) as error:
        await EchoTool().execute({"path": "/a", "max_chars": 99_999}, ctx)
    assert error.value.code == "invalid_input"
    assert error.value.details["errors"][0]["loc"] == ("max_chars",)
    assert "input" not in error.value.details["errors"][0]
