from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.mcp import McpError, McpResult
from bodhan_agent.tools.base import LocalToolError, ToolContext, register, write_new
from bodhan_agent.tools.make_subtitles import (
    CUE_CHARS,
    LINE_CHARS,
    MAX_CUE_S,
    Cue,
    MakeSubtitlesTool,
    Usage,
    _cue_texts,
    _groups,
    _parse_numbered,
    _srt,
    _timestamp,
    _wrap,
)

HINDI_LONG = "पहला वाक्य यहाँ है। " * 6
HINDI_SHORT = "दूसरा छोटा वाक्य।"
USAGE = {
    "model_calls": 1,
    "http_attempts": 1,
    "estimated_cost_inr": 0.01,
    "billing_uncertain": False,
    "possibly_billed_failed_attempts": 0,
    "cache_hits": 0,
    "latency_ms": 5,
}
SRT_BLOCK = re.compile(r"(\d+)\n(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})\n(.+?)(?:\n\n|\n?$)", re.S)


def english(text: str) -> str:
    return re.sub(r"[\u0900-\u097f]+", "word", text)


def ok(data: dict[str, Any]) -> McpResult:
    return McpResult(tool="translate_text", ok=True, data={"usage": USAGE, **data}, latency_ms=1)


class FakeMcp:
    def __init__(self, respond: Callable[[str], McpResult] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.deadlines: list[float | None] = []
        self.respond = respond or (lambda text: ok({"translation": english(text), "translation_is_preview": False}))

    async def call(self, name: str, arguments: dict[str, Any], *, deadline: float | None = None) -> McpResult:
        assert name == "translate_text"
        self.calls.append(arguments)
        self.deadlines.append(deadline)
        return self.respond(arguments["text"])


def segment(start: float, end: float, text: str, status: str = "transcribed") -> dict[str, Any]:
    return {"start_s": start, "end_s": end, "text": text, "status": status}


DEFAULT_SEGMENTS = [
    segment(0, 20, HINDI_LONG),
    segment(20, 24, "", "silent"),
    segment(24, 30, HINDI_SHORT, "suspect"),
]


@pytest.fixture
def mcp() -> FakeMcp:
    return FakeMcp()


@pytest.fixture
def ctx(tmp_path: Path, mcp: FakeMcp) -> ToolContext:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    settings.work_dir.mkdir(parents=True)
    settings.artifacts_dir.mkdir(parents=True)
    return ToolContext(settings=settings, ledger=Ledger(), mcp=mcp, deadline=123.0, seq=4)  # type: ignore[arg-type]


def add_segments(
    ctx: ToolContext,
    segments: list[dict[str, Any]] | None = None,
    language: str | None = "hi",
    name: str = "talk.segments.json",
    kind: str = "segments",
    raw: bytes | None = None,
) -> Artifact:
    document = {"language": language, "segments": DEFAULT_SEGMENTS if segments is None else segments}
    data = raw if raw is not None else json.dumps(document, ensure_ascii=False).encode("utf-8")
    artifact = register(ctx, write_new(ctx.settings.work_dir / name, data), kind, "bodhan", tool="transcribe_audio")
    ctx.ledger.set_status(artifact.id, "ok")
    return artifact


async def make(ctx: ToolContext, source: Artifact, target: str = "en", **arguments: Any) -> Any:
    return await MakeSubtitlesTool().execute(
        {"segments_path": source.path, "target_language": target, **arguments}, ctx
    )


async def make_error(ctx: ToolContext, source: Artifact, target: str = "en", **arguments: Any) -> LocalToolError:
    with pytest.raises(LocalToolError) as error:
        await make(ctx, source, target, **arguments)
    return error.value


def parse_srt(text: str) -> list[tuple[int, float, float, str]]:
    def seconds(stamp: str) -> float:
        hours, minutes, rest = stamp.split(":")
        secs, millis = rest.split(",")
        return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(millis) / 1000

    return [(int(index), seconds(start), seconds(end), body) for index, start, end, body in SRT_BLOCK.findall(text)]


def test_definition_is_named_make_subtitles() -> None:
    definition = MakeSubtitlesTool().definition()
    assert definition["function"]["name"] == "make_subtitles"
    assert definition["function"]["parameters"]["required"] == ["reason", "segments_path", "target_language"]


def test_parse_numbered_accepts_exact_markers() -> None:
    assert _parse_numbered("[1] one\n\n[2] two", [1, 2]) == {1: "one", 2: "two"}
    assert _parse_numbered("[ 3 ]a [4]b", [3, 4]) == {3: "a", 4: "b"}


def test_parse_numbered_accepts_indic_digits() -> None:
    assert _parse_numbered("[१] एक [२] दो", [1, 2]) == {1: "एक", 2: "दो"}


@pytest.mark.parametrize(
    "text",
    ["[1] one", "[1] one [3] three", "[2] two [1] one", "lead [1] one [2] two", "[1] one [1] again [2] two", "none"],
)
def test_parse_numbered_rejects_mismatch(text: str) -> None:
    assert _parse_numbered(text, [1, 2]) is None


def test_groups_respect_limit() -> None:
    items = [(1, "a" * 40), (2, "b" * 40), (3, "c" * 40)]
    groups = _groups(items, 100)
    assert [[number for number, _ in group] for group in groups] == [[1, 2], [3]]


def test_groups_keep_oversized_item_alone() -> None:
    groups = _groups([(1, "a" * 500), (2, "b")], 100)
    assert [[number for number, _ in group] for group in groups] == [[1], [2]]


def test_cue_texts_fit_length_and_duration() -> None:
    text = " ".join(["word"] * 60)
    pieces = _cue_texts(text, 25.0)
    total = sum(len(piece) for piece in pieces)
    assert " ".join(pieces) == text
    assert all(len(piece) <= CUE_CHARS for piece in pieces)
    assert all(25.0 * len(piece) / total <= MAX_CUE_S + 1e-6 for piece in pieces)


def test_cue_texts_short_text_is_one_cue() -> None:
    assert _cue_texts("Hello there.", 3.0) == ["Hello there."]


def test_cue_texts_empty() -> None:
    assert _cue_texts("   ", 5.0) == []


def test_cue_texts_split_very_long_word() -> None:
    pieces = _cue_texts("x" * 200, 5.0)
    assert "".join(pieces) == "x" * 200
    assert all(len(piece) <= CUE_CHARS for piece in pieces)


def test_wrap_breaks_near_middle() -> None:
    text = "one two three four five six seven eight nine ten"
    wrapped = _wrap(text)
    assert wrapped.count("\n") == 1
    assert all(len(line) <= LINE_CHARS for line in wrapped.split("\n"))
    assert wrapped.replace("\n", " ") == text
    assert _wrap("short line") == "short line"


def test_timestamp_format() -> None:
    assert _timestamp(0) == "00:00:00,000"
    assert _timestamp(3661.2345) == "01:01:01,234"
    assert _timestamp(59.9996) == "00:01:00,000"


def test_srt_layout() -> None:
    text = _srt([Cue(0.0, 1.5, "Hello."), Cue(1.5, 3.0, "World.")])
    assert text == "1\n00:00:00,000 --> 00:00:01,500\nHello.\n\n2\n00:00:01,500 --> 00:00:03,000\nWorld.\n"


def test_usage_sums_and_flags() -> None:
    usage = Usage()
    assert usage.summary() is None
    usage.add(ok({}))
    usage.add(
        McpResult(
            tool="translate_text",
            ok=False,
            error=McpError(code="tool_timeout", message="slow", uncertain=True),
            latency_ms=1,
        )
    )
    summary = usage.summary()
    assert summary is not None
    assert (summary["model_calls"], summary["estimated_cost_inr"], summary["billing_uncertain"]) == (1, 0.01, True)


async def test_translates_in_one_batch_and_writes_srt(ctx: ToolContext, mcp: FakeMcp) -> None:
    source = add_segments(ctx)
    result = await make(ctx, source)
    assert len(mcp.calls) == 1
    assert mcp.calls[0]["target_language"] == "en"
    assert mcp.calls[0]["text"].startswith("[1] ") and "\n\n[2] " in mcp.calls[0]["text"]
    assert mcp.deadlines == [123.0]
    path = ctx.settings.work_dir / "talk.en.srt"
    assert result.data["path"] == str(path.resolve())
    assert result.data["cues"] == len(parse_srt(path.read_text(encoding="utf-8")))
    assert (result.data["segments"], result.data["translated"], result.data["language"]) == (2, True, "en")
    assert result.data["duration_s"] == 30.0
    assert result.usage is not None and result.usage["model_calls"] == 1


async def test_srt_cues_are_ordered_short_and_inside_windows(ctx: ToolContext) -> None:
    source = add_segments(ctx)
    result = await make(ctx, source)
    cues = parse_srt(Path(result.data["path"]).read_text(encoding="utf-8"))
    assert [cue[0] for cue in cues] == list(range(1, len(cues) + 1))
    for (_, start, end, body), following in zip(cues, [*cues[1:], None], strict=True):
        assert end > start
        assert end - start <= MAX_CUE_S + 0.001
        assert all(len(line) <= LINE_CHARS for line in body.split("\n"))
        if following is not None:
            assert following[1] >= end
    assert not any(20.0 < start < 24.0 for _, start, _, _ in cues)
    assert cues[-1][1:3] == (24.0, 30.0)


async def test_registers_code_composed_subtitles(ctx: ToolContext) -> None:
    source = add_segments(ctx)
    result = await make(ctx, source)
    artifact = ctx.ledger.get(result.data["artifact"])
    assert artifact is not None
    assert (artifact.kind, artifact.producer, artifact.tool, artifact.sources) == (
        "subtitles",
        "code_composed",
        "make_subtitles",
        [source.id],
    )
    assert (artifact.language, artifact.language_source, artifact.script, artifact.indic_text) == (
        "en",
        "requested",
        "Latin",
        False,
    )
    assert artifact.created_seq == 4
    assert artifact.step_status == "warning"
    assert any("suspect" in warning for warning in artifact.warnings)
    assert result.artifact_ids == [artifact.id]


async def test_clean_transcript_is_ok_without_warnings(ctx: ToolContext) -> None:
    source = add_segments(ctx, [segment(0, 5, HINDI_SHORT), segment(5, 9, HINDI_SHORT)])
    result = await make(ctx, source)
    assert "warnings" not in result.data
    artifact = ctx.ledger.get(result.data["artifact"])
    assert artifact is not None and artifact.step_status == "ok"


async def test_lost_markers_fall_back_per_segment(ctx: ToolContext) -> None:
    def respond(text: str) -> McpResult:
        translated = english(text).replace("[2]", "") if "[2]" in text else english(text)
        return ok({"translation": translated, "translation_is_preview": False})

    ctx.mcp.respond = respond  # type: ignore[union-attr]
    source = add_segments(ctx)
    result = await make(ctx, source)
    calls = ctx.mcp.calls  # type: ignore[union-attr]
    assert len(calls) == 3
    assert calls[1]["text"] == HINDI_LONG and calls[2]["text"] == HINDI_SHORT
    assert any("one by one" in warning for warning in result.data["warnings"])
    assert result.usage is not None and result.usage["model_calls"] == 3
    srt = Path(result.data["path"]).read_text(encoding="utf-8")
    assert "[" not in srt


async def test_single_segment_is_sent_without_markers(ctx: ToolContext, mcp: FakeMcp) -> None:
    source = add_segments(ctx, [segment(0, 5, HINDI_SHORT)])
    result = await make(ctx, source)
    assert mcp.calls[0]["text"] == HINDI_SHORT
    assert "[1]" not in Path(result.data["path"]).read_text(encoding="utf-8")


async def test_small_chunk_limit_makes_several_batches(
    ctx: ToolContext, mcp: FakeMcp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BODHAN_TRANSLATE_CHUNK_CHARS", "200")
    segments = [segment(index * 5, index * 5 + 5, HINDI_LONG[:90]) for index in range(4)]
    source = add_segments(ctx, segments)
    await make(ctx, source)
    assert [re.findall(r"\[(\d)\]", call["text"]) for call in mcp.calls] == [["1", "2"], ["3", "4"]]
    assert all(len(call["text"]) <= 200 for call in mcp.calls)


async def test_same_language_copies_transcript_without_bodhan(ctx: ToolContext, mcp: FakeMcp) -> None:
    source = add_segments(ctx, [segment(0, 5, HINDI_SHORT)])
    result = await make(ctx, source, target="hi")
    assert mcp.calls == []
    assert result.usage is None
    assert result.data["translated"] is False and "not translated" in result.data["note"]
    assert HINDI_SHORT in Path(result.data["path"]).read_text(encoding="utf-8")
    artifact = ctx.ledger.get(result.data["artifact"])
    assert artifact is not None
    assert (artifact.script, artifact.indic_text, artifact.language) == ("Devanagari", True, "hi")


async def test_unknown_transcript_language_is_translated(ctx: ToolContext, mcp: FakeMcp) -> None:
    source = add_segments(ctx, [segment(0, 5, HINDI_SHORT)], language=None)
    await make(ctx, source, target="hi")
    assert len(mcp.calls) == 1


async def test_reads_full_translation_when_preview(ctx: ToolContext, tmp_path: Path) -> None:
    full = tmp_path / "full.txt"
    full.write_text("[1] Full first part.\n\n[2] Full second part.", encoding="utf-8")
    ctx.mcp.respond = lambda text: ok(  # type: ignore[union-attr]
        {"translation": "[1] Full", "translation_is_preview": True, "output_path": str(full)}
    )
    source = add_segments(ctx)
    result = await make(ctx, source)
    srt = Path(result.data["path"]).read_text(encoding="utf-8")
    assert "Full second part." in srt


async def test_unreadable_preview_file_is_error_with_usage(ctx: ToolContext, tmp_path: Path) -> None:
    ctx.mcp.respond = lambda text: ok(  # type: ignore[union-attr]
        {"translation": "x", "translation_is_preview": True, "output_path": str(tmp_path / "missing.txt")}
    )
    error = await make_error(ctx, add_segments(ctx))
    assert error.code == "protocol_error"
    assert error.details["usage"]["model_calls"] == 1


async def test_translate_failure_reports_usage_and_uncertainty(ctx: ToolContext) -> None:
    failed = McpResult(
        tool="translate_text",
        ok=False,
        error=McpError(
            code="tool_timeout",
            message="no answer",
            hint="try later",
            retryable=True,
            uncertain=True,
            details={"usage": {**USAGE, "estimated_cost_inr": 0.02}},
        ),
        latency_ms=1,
    )
    ctx.mcp.respond = lambda text: failed  # type: ignore[union-attr]
    source = add_segments(ctx)
    error = await make_error(ctx, source)
    assert (error.code, error.retryable, error.hint) == ("tool_timeout", True, "try later")
    assert error.details["uncertain"] is True
    assert error.details["usage"]["estimated_cost_inr"] == 0.02
    assert error.details["usage"]["billing_uncertain"] is True
    assert list(ctx.settings.work_dir.glob("*.srt")) == []
    assert list(ctx.ledger.artifacts) == [source.id]


async def test_failure_after_success_keeps_earlier_usage(ctx: ToolContext) -> None:
    answers = iter(
        [
            ok({"translation": "lost markers", "translation_is_preview": False}),
            McpResult(
                tool="translate_text",
                ok=False,
                error=McpError(code="rate_limited", message="slow down", retryable=True),
                latency_ms=1,
            ),
        ]
    )
    ctx.mcp.respond = lambda text: next(answers)  # type: ignore[union-attr]
    error = await make_error(ctx, add_segments(ctx))
    assert error.code == "rate_limited"
    assert error.details["uncertain"] is False
    assert error.details["usage"]["model_calls"] == 1
    assert error.details["usage"]["billing_uncertain"] is True


async def test_no_mcp_session(ctx: ToolContext) -> None:
    source = add_segments(ctx)
    error = await make_error(ToolContext(settings=ctx.settings, ledger=ctx.ledger), source)
    assert error.code == "server_unavailable"


async def test_empty_translation_is_warned_and_skipped(ctx: ToolContext) -> None:
    ctx.mcp.respond = lambda text: ok(  # type: ignore[union-attr]
        {"translation": "[1] Fine words here. [2] ", "translation_is_preview": False}
    )
    result = await make(ctx, add_segments(ctx))
    assert any("came back empty" in warning for warning in result.data["warnings"])
    cues = parse_srt(Path(result.data["path"]).read_text(encoding="utf-8"))
    assert all(end <= 20.0 for _, _, end, _ in cues)


async def test_all_empty_translations_fail(ctx: ToolContext) -> None:
    ctx.mcp.respond = lambda text: ok({"translation": "  ", "translation_is_preview": False})  # type: ignore[union-attr]
    error = await make_error(ctx, add_segments(ctx, [segment(0, 5, HINDI_SHORT)]))
    assert error.code == "empty_output"
    assert error.details["usage"]["model_calls"] == 1
    assert list(ctx.settings.work_dir.glob("*.srt")) == []


async def test_writes_into_artifacts(ctx: ToolContext) -> None:
    target = ctx.settings.artifacts_dir / "talk.en.srt"
    result = await make(ctx, add_segments(ctx), output_path=str(target))
    assert result.data["path"] == str(target.resolve())
    assert target.exists()


async def test_never_overwrites(ctx: ToolContext, mcp: FakeMcp) -> None:
    existing = ctx.settings.work_dir / "talk.en.srt"
    existing.write_text("old", encoding="utf-8")
    error = await make_error(ctx, add_segments(ctx))
    assert error.code == "file_exists"
    assert existing.read_text(encoding="utf-8") == "old"
    assert mcp.calls == []


async def test_rejects_wrong_suffix(ctx: ToolContext) -> None:
    error = await make_error(ctx, add_segments(ctx), output_path="subs.txt")
    assert error.code == "invalid_input"


async def test_rejects_unsupported_language(ctx: ToolContext) -> None:
    error = await make_error(ctx, add_segments(ctx), target="xx")
    assert error.code == "invalid_input"


async def test_rejects_non_segments_file(ctx: ToolContext) -> None:
    source = add_segments(ctx, name="talk.txt", kind="text")
    error = await make_error(ctx, source)
    assert error.code == "unsupported_input"


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b'{"language": "hi"}',
        b"[1, 2]",
        b'{"segments": [{"start_s": 5, "end_s": 1, "text": "x", "status": "transcribed"}]}',
        b'{"segments": [{"start_s": 0, "end_s": 1, "text": "x", "status": "odd"}]}',
    ],
)
async def test_rejects_malformed_segments(ctx: ToolContext, mcp: FakeMcp, raw: bytes) -> None:
    error = await make_error(ctx, add_segments(ctx, raw=raw))
    assert error.code == "invalid_input"
    assert mcp.calls == []


async def test_rejects_transcript_without_speech(ctx: ToolContext, mcp: FakeMcp) -> None:
    source = add_segments(ctx, [segment(0, 5, "", "silent"), segment(5, 9, "   ")])
    error = await make_error(ctx, source)
    assert error.code == "empty_input"
    assert mcp.calls == []


async def test_rejects_unusable_segments(ctx: ToolContext) -> None:
    source = add_segments(ctx)
    ctx.ledger.set_status(source.id, "failed")
    error = await make_error(ctx, source)
    assert error.code == "artifact_not_usable"


async def test_segments_are_sorted_by_start(ctx: ToolContext) -> None:
    source = add_segments(ctx, [segment(10, 14, "Later words."), segment(0, 4, "Early words.")], language="en")
    result = await make(ctx, source)
    cues = parse_srt(Path(result.data["path"]).read_text(encoding="utf-8"))
    assert [body for _, _, _, body in cues] == ["Early words.", "Later words."]
