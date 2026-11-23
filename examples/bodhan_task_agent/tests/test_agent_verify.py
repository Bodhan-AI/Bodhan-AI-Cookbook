from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.core.config import RunSettings
from bodhan_agent.pipeline.verify import (
    apply_step,
    expected_scripts,
    parse_srt,
    script_share,
    step_verdict,
    verify_deliverable,
)
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.schemas.plan import Deliverable
from bodhan_agent.schemas.report import DeliverableReport
from bodhan_agent.schemas.verify import StepVerdict
from bodhan_agent.tools.base import ToolContext, register, write_new

LABEL = "AI-generated speech (Bodhan Indic-Speak)"
TABLE_EN = "| a | b |\n|---|---|\n| 1 | 2 |\n\nPlants make food from sunlight."
TABLE_TA = "| அ | ஆ |\n|---|---|\n| 1 | 2 |\n\nதாவரங்கள் சூரிய ஒளியில் இருந்து உணவு தயாரிக்கின்றன."
SRT = "1\n00:00:00,000 --> 00:00:05,000\nHello there.\n\n2\n00:00:05,000 --> 00:00:29,000\nGood bye.\n"
SPEECH = {
    "duration_s": 40.0,
    "label": LABEL,
    "voice_gender": "female",
    "format": "wav",
    "verification": {"status": "passed", "word_recall": 0.93},
}


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    settings.work_dir.mkdir(parents=True)
    return ToolContext(settings=settings, ledger=Ledger())


def add(
    ctx: ToolContext, name: str, data: str | bytes, kind: str, producer: str = "bodhan", status: str | None = "ok",
    **facts: Any,
) -> Artifact:  # fmt: skip
    raw = data.encode("utf-8") if isinstance(data, str) else data
    artifact = register(ctx, write_new(ctx.settings.work_dir / name, raw), kind, producer, **facts)
    if status is not None:
        ctx.ledger.set_status(artifact.id, status)
    return artifact


def deliverable(kind: str, language: str | None = "en", **fields: Any) -> Deliverable:
    return Deliverable(id="D1", kind=kind, language=language, description="x", **fields)


def failing(verdict: Any) -> dict[str, str]:
    return {check.name: check.status for check in verdict.checks if check.status != "passed"}


def statuses(verdict: Any) -> dict[str, str]:
    return {check.name: check.status for check in verdict.checks}


def english_source(ctx: ToolContext, text: str = TABLE_EN) -> Artifact:
    return add(ctx, "notes.en.md", text, "markdown", "llm_english", language="en")


def tamil_translation(ctx: ToolContext, text: str = TABLE_TA, name: str = "notes.ta.md", **step: Any) -> Artifact:
    source = english_source(ctx) if not ctx.ledger.artifacts else ctx.ledger.get("A1")
    assert source is not None
    artifact = add(
        ctx, name, text, "markdown", tool="translate_text", sources=[source.id], language="ta",
        indic_text=True, status=None,
    )  # fmt: skip
    data = {"verification_status": "passed", "translated_chars": len(text), "script": "native", **step}
    apply_step(ctx.ledger, [artifact.id], step_verdict("translate_text", data))
    return artifact


def speech(ctx: ToolContext, name: str = "s.ta.wav", duration: float = 40.0, **data: Any) -> Artifact:
    artifact = add(
        ctx, name, b"RIFF", "audio", tool="synthesize_speech", language="ta", duration_s=duration, status=None
    )
    apply_step(ctx.ledger, [artifact.id], step_verdict("synthesize_speech", {**SPEECH, "duration_s": duration, **data}))
    return artifact


def subtitles(ctx: ToolContext, text: str = SRT, name: str = "t.en.srt", duration: float | None = 30.0) -> Artifact:
    segments = add(ctx, f"{name}.segments.json", "{}", "segments", tool="transcribe_audio", duration_s=duration)
    return add(
        ctx, name, text, "subtitles", "code_composed", tool="make_subtitles", sources=[segments.id], language="en"
    )


def test_step_verdict_transcribe_passed() -> None:
    verdict = step_verdict(
        "transcribe_audio",
        {"transcript": "hello", "chunks": 3, "silent_chunks": 1, "suspect_chunks": 0, "verification_status": "passed"},
    )
    assert verdict == StepVerdict(
        status="ok",
        tool_checks={"verification_status": "passed", "chunks": 3, "silent_chunks": 1, "suspect_chunks": 0},
    )


def test_step_verdict_transcribe_empty_fails() -> None:
    verdict = step_verdict("transcribe_audio", {"transcript": " ", "chunks": 2, "silent_chunks": 0})
    assert verdict.status == "failed"
    assert verdict.warnings[0] == "The transcript is empty."


def test_step_verdict_transcribe_all_suspect_warns() -> None:
    verdict = step_verdict(
        "transcribe_audio", {"transcript": "x", "chunks": 2, "silent_chunks": 0, "suspect_chunks": 2}
    )
    assert verdict.status == "warning"
    assert "suspect" in verdict.warnings[0]


def test_step_verdict_server_warnings_become_warning() -> None:
    verdict = step_verdict(
        "transcribe_audio",
        {"transcript": "x", "chunks": 1, "verification_status": "warning", "warnings": ["Model returned little."]},
    )
    assert (verdict.status, verdict.warnings) == ("warning", ["Model returned little."])


def test_step_verdict_warning_status_without_text_gets_generic_warning() -> None:
    verdict = step_verdict("translate_text", {"translated_chars": 5, "verification_status": "warning"})
    assert verdict.status == "warning"
    assert verdict.warnings == ["The tool reported a verification warning."]


def test_step_verdict_translate() -> None:
    ok = step_verdict("translate_text", {"translated_chars": 10, "verification_status": "passed", "script": "roman"})
    assert ok.status == "ok" and ok.tool_checks["script"] == "roman"
    empty = step_verdict("translate_text", {"translated_chars": 0, "verification_status": "passed"})
    assert empty.status == "failed"


@pytest.mark.parametrize(
    ("data", "status"),
    [
        ({"pages_read": [1], "text": "x"}, "ok"),
        ({"pages_read": [1, 2], "text": "x", "warnings": ["low confidence"]}, "warning"),
        ({"pages_read": [], "text": "x"}, "failed"),
        ({"pages_read": [1], "text": "  "}, "failed"),
    ],
)
def test_step_verdict_ocr(data: dict[str, Any], status: str) -> None:
    verdict = step_verdict("ocr_document", data)
    assert verdict.status == status
    assert verdict.tool_checks == {"pages_read": len(data["pages_read"])}


def test_step_verdict_ocr_keeps_unread_pages() -> None:
    verdict = step_verdict("ocr_document", {"pages_read": [1, 3], "unread_pages": [2], "text": "x"})
    assert verdict.tool_checks == {"pages_read": 2, "unread_pages": [2]}


def test_step_verdict_speech() -> None:
    passed = step_verdict("synthesize_speech", SPEECH)
    assert passed.status == "ok"
    assert passed.tool_checks == {
        "speech_verification": "passed",
        "word_recall": 0.93,
        "label": LABEL,
        "voice_gender": "female",
        "format": "wav",
    }
    weak = step_verdict("synthesize_speech", {**SPEECH, "verification": {"status": "warning", "reason": "Words lost."}})
    assert (weak.status, weak.warnings) == ("warning", ["Words lost."])
    silent = step_verdict("synthesize_speech", {**SPEECH, "duration_s": 0})
    assert silent.status == "failed"


def test_step_verdict_unknown_tool_uses_warnings() -> None:
    assert step_verdict("other", {}).status == "ok"
    assert step_verdict("other", {"warnings": ["x"]}).status == "warning"


def test_apply_step_merges_facts_and_sets_status(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.md", "x", "markdown", tool="translate_text", status=None, tool_checks={"old": 1})
    apply_step(ctx.ledger, [artifact.id, "A99"], StepVerdict(status="warning", warnings=["w"], tool_checks={"n": 2}))
    assert artifact.tool_checks == {"old": 1, "n": 2}
    assert (artifact.step_status, artifact.warnings) == ("warning", ["w"])


@pytest.mark.parametrize(
    ("language", "scripts"),
    [
        ("en", {"Latin"}),
        ("ta", {"Tamil"}),
        ("hi", {"Devanagari"}),
        ("ks-Deva", {"Devanagari"}),
        ("sd-Arab", {"Arabic"}),
        ("mni-Beng", {"Bengali"}),
        ("mni", {"Bengali", "Meetei Mayek"}),
        (None, None),
        ("xx", None),
    ],
)
def test_expected_scripts(language: str | None, scripts: set[str] | None) -> None:
    result = expected_scripts(language)
    assert (set(result) if result is not None else None) == scripts


def test_expected_scripts_roman_translation_is_latin(ctx: ToolContext) -> None:
    artifact = add(ctx, "r.md", "x", "markdown", tool="translate_text", tool_checks={"script": "roman"})
    assert expected_scripts("hi", artifact) == frozenset({"Latin"})


def test_script_share() -> None:
    assert script_share("தமிழ் abc", frozenset({"Tamil"})) == pytest.approx(5 / 8)
    assert script_share("123 !!", frozenset({"Latin"})) is None


def test_parse_srt() -> None:
    assert parse_srt(SRT) == [(0.0, 5.0, "Hello there."), (5.0, 29.0, "Good bye.")]
    assert parse_srt(SRT.replace("\n", "\r\n")) == parse_srt(SRT)
    assert parse_srt("1\n01:02:03,456 --> 01:02:04,000\nTwo\nlines\n") == [(3723.456, 3724.0, "Two\nlines")]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not srt",
        "2\n00:00:00,000 --> 00:00:01,000\nWrong number\n",
        "1\n00:00:00.000 --> 00:00:01,000\nDot separator\n",
        "1\n00:00:00,000 --> 00:00:01,000\n",
    ],
)
def test_parse_srt_rejects(text: str) -> None:
    assert parse_srt(text) is None


def test_tamil_markdown_is_verified(ctx: ToolContext) -> None:
    artifact = tamil_translation(ctx)
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "ta", constraints={"keep_tables": True}), artifact)
    assert verdict.status == "verified"
    assert statuses(verdict) == {
        "file_intact": "passed",
        "provenance": "passed",
        "text_readable": "passed",
        "target_script": "passed",
        "translation_structure": "passed",
        "length_coverage": "passed",
        "tables_kept": "passed",
    }


def test_english_text_labelled_tamil_fails_on_content(ctx: ToolContext) -> None:
    english_source(ctx)
    artifact = add(
        ctx, "fake.ta.md", "Plants make food from sunlight.", "markdown", tool="translate_text",
        sources=["A1"], language="ta",
    )  # fmt: skip
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "ta"), artifact)
    assert verdict.status == "failed"
    assert failing(verdict)["target_script"] == "failed"


def test_missing_language_metadata_is_checked_from_content(ctx: ToolContext) -> None:
    english_source(ctx)
    artifact = add(ctx, "x.md", "Only English here.", "markdown", tool="translate_text", sources=["A1"])
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "ta"), artifact)
    assert verdict.status == "failed"
    assert failing(verdict)["target_script"] == "failed"


def test_mixed_script_is_warning(ctx: ToolContext) -> None:
    text = "தாவரங்கள் உணவு Photosynthesis chlorophyll"
    artifact = tamil_translation(ctx, text)
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "ta"), artifact)
    assert verdict.status == "warning"
    check = next(item for item in verdict.checks if item.name == "target_script")
    assert check.status == "warning" and check.threshold == 0.8 and 0.3 <= (check.value or 0) < 0.8


def test_translation_warning_and_length_ratio(ctx: ToolContext) -> None:
    artifact = tamil_translation(ctx, "தமிழ்", verification_status="warning")
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "ta"), artifact)
    assert failing(verdict) == {
        "step_status": "warning",
        "translation_structure": "warning",
        "length_coverage": "warning",
    }


def test_lost_tables_fail_and_changed_tables_warn(ctx: ToolContext) -> None:
    lost = tamil_translation(ctx, "தாவரங்கள் சூரிய ஒளியில் இருந்து உணவு தயாரிக்கின்றன. " * 2)
    wanted = deliverable("markdown", "ta", constraints={"keep_tables": True})
    assert failing(verify_deliverable(ctx.ledger, wanted, lost))["tables_kept"] == "failed"
    changed = tamil_translation(ctx, "| அ |\n|---|\n| 1 |\n\nதாவரங்கள் உணவு தயாரிக்கின்றன.", name="c.ta.md")
    assert failing(verify_deliverable(ctx.ledger, wanted, changed))["tables_kept"] == "warning"


def test_tables_skipped_without_source(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.ta.md", TABLE_TA, "markdown", tool="ocr_document", language="ta", indic_text=True)
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "ta", constraints={"keep_tables": True}), artifact)
    assert statuses(verdict)["tables_kept"] == "skipped"
    assert verdict.status == "verified"


@pytest.mark.parametrize(("data", "detail"), [(b"", "empty"), (b"\xff\xfe bad", "UTF-8")])
def test_unreadable_or_empty_text_fails(ctx: ToolContext, data: bytes, detail: str) -> None:
    artifact = add(ctx, "a.txt", data, "text", tool="translate_text", language="en")
    verdict = verify_deliverable(ctx.ledger, deliverable("text", "en"), artifact)
    assert verdict.status == "failed"
    assert detail in (verdict.checks[-1].detail or "")


def test_text_without_target_language_skips_script(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.txt", "Hello.", "text", tool="translate_text")
    verdict = verify_deliverable(ctx.ledger, deliverable("text", None), artifact)
    assert statuses(verdict)["target_script"] == "skipped"


def test_audio_is_verified_with_label(ctx: ToolContext) -> None:
    artifact = speech(ctx)
    wanted = deliverable("audio", "ta", format="wav", constraints={"duration_s": 42, "voice_gender": "female"})
    verdict = verify_deliverable(ctx.ledger, wanted, artifact)
    assert verdict.status == "verified"
    assert verdict.label == LABEL
    assert statuses(verdict) == {
        "file_intact": "passed",
        "provenance": "passed",
        "audio_duration": "passed",
        "speech_word_recall": "passed",
        "duration_target": "passed",
        "voice_gender": "passed",
        "ai_label": "passed",
    }


def test_audio_target_misses_are_warnings(ctx: ToolContext) -> None:
    artifact = speech(ctx, voice_gender="male")
    wanted = deliverable("audio", "ta", format="wav", constraints={"duration_s": 60, "voice_gender": "female"})
    verdict = verify_deliverable(ctx.ledger, wanted, artifact)
    assert verdict.status == "warning"
    assert failing(verdict) == {"duration_target": "warning", "voice_gender": "warning"}


def test_low_word_recall_is_warning(ctx: ToolContext) -> None:
    artifact = speech(ctx, verification={"status": "warning", "word_recall": 0.5, "reason": "Words lost."})
    verdict = verify_deliverable(ctx.ledger, deliverable("audio", "ta", format="wav"), artifact)
    assert failing(verdict) == {"step_status": "warning", "speech_word_recall": "warning"}
    check = next(item for item in verdict.checks if item.name == "speech_word_recall")
    assert (check.value, check.threshold) == (0.5, 0.8)


def test_passed_status_with_low_recall_is_warning(ctx: ToolContext) -> None:
    artifact = speech(ctx, verification={"status": "passed", "word_recall": 0.7})
    verdict = verify_deliverable(ctx.ledger, deliverable("audio", "ta", format="wav"), artifact)
    assert failing(verdict) == {"speech_word_recall": "warning"}


def test_audio_without_label_fails(ctx: ToolContext) -> None:
    artifact = speech(ctx, label=None)
    verdict = verify_deliverable(ctx.ledger, deliverable("audio", "ta", format="wav"), artifact)
    assert verdict.status == "failed"
    assert failing(verdict) == {"ai_label": "failed"}
    assert verdict.label is None


def test_audio_without_duration_fails(ctx: ToolContext) -> None:
    artifact = add(
        ctx, "s.wav", b"RIFF", "audio", tool="synthesize_speech", language="ta",
        tool_checks={"label": LABEL, "speech_verification": "passed", "word_recall": 0.9},
    )  # fmt: skip
    verdict = verify_deliverable(ctx.ledger, deliverable("audio", "ta", format="wav"), artifact)
    assert failing(verdict) == {"audio_duration": "failed"}


def test_subtitles_are_verified(ctx: ToolContext) -> None:
    artifact = subtitles(ctx)
    verdict = verify_deliverable(ctx.ledger, deliverable("subtitles", "en"), artifact)
    assert verdict.status == "verified"
    assert statuses(verdict) == {
        "file_intact": "passed",
        "provenance": "passed",
        "srt_parses": "passed",
        "cues_ordered": "passed",
        "speech_coverage": "passed",
        "target_script": "passed",
    }


def test_subtitles_coverage_uses_audio_duration(ctx: ToolContext) -> None:
    audio = add(ctx, "talk.mp3", b"ID3", "audio", tool="synthesize_speech", duration_s=100.0)
    segments = add(ctx, "t.segments.json", "{}", "segments", tool="transcribe_audio", sources=[audio.id])
    artifact = add(
        ctx, "t.en.srt", SRT, "subtitles", "code_composed", tool="make_subtitles", sources=[segments.id],
        language="en",
    )  # fmt: skip
    verdict = verify_deliverable(ctx.ledger, deliverable("subtitles", "en"), artifact)
    assert failing(verdict) == {"speech_coverage": "warning"}


def test_subtitles_without_known_duration_skip_coverage(ctx: ToolContext) -> None:
    artifact = subtitles(ctx, duration=None)
    verdict = verify_deliverable(ctx.ledger, deliverable("subtitles", "en"), artifact)
    assert "speech_coverage" not in statuses(verdict)


@pytest.mark.parametrize(
    "text",
    [
        "1\n00:00:05,000 --> 00:00:01,000\nBackwards\n",
        "1\n00:00:00,000 --> 00:00:10,000\nOne\n\n2\n00:00:05,000 --> 00:00:29,000\nOverlap\n",
    ],
)
def test_bad_cue_order_fails(ctx: ToolContext, text: str) -> None:
    verdict = verify_deliverable(ctx.ledger, deliverable("subtitles", "en"), subtitles(ctx, text))
    assert failing(verdict)["cues_ordered"] == "failed"


def test_invalid_srt_fails(ctx: ToolContext) -> None:
    verdict = verify_deliverable(ctx.ledger, deliverable("subtitles", "en"), subtitles(ctx, "garbage"))
    assert failing(verdict) == {"srt_parses": "failed"}


def test_subtitles_in_wrong_script_fail(ctx: ToolContext) -> None:
    artifact = subtitles(ctx)
    verdict = verify_deliverable(ctx.ledger, deliverable("subtitles", "ta"), artifact)
    assert failing(verdict) == {"language": "failed", "target_script": "failed"}


def test_missing_artifact(ctx: ToolContext) -> None:
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown"), None)
    assert (verdict.status, verdict.checks) == ("missing", [])


def test_changed_file_fails_without_further_checks(ctx: ToolContext) -> None:
    artifact = subtitles(ctx)
    Path(artifact.path).write_text("x", encoding="utf-8")
    verdict = verify_deliverable(ctx.ledger, deliverable("subtitles", "en"), artifact)
    assert [(check.name, check.status) for check in verdict.checks] == [("file_intact", "failed")]


def test_deleted_file_fails(ctx: ToolContext) -> None:
    artifact = subtitles(ctx)
    Path(artifact.path).unlink()
    verdict = verify_deliverable(ctx.ledger, deliverable("subtitles", "en"), artifact)
    assert "no longer exists" in (verdict.checks[0].detail or "")


def test_wrong_kind_and_language_fail(ctx: ToolContext) -> None:
    artifact = tamil_translation(ctx)
    verdict = verify_deliverable(ctx.ledger, deliverable("subtitles", "en"), artifact)
    assert {"kind", "language"} <= set(failing(verdict))
    assert verdict.status == "failed"


def test_wrong_audio_format_fails(ctx: ToolContext) -> None:
    artifact = speech(ctx)
    verdict = verify_deliverable(ctx.ledger, deliverable("audio", "ta", format="mp3"), artifact)
    assert failing(verdict).get("format") == "failed"


def test_text_deliverable_accepts_markdown_file(ctx: ToolContext) -> None:
    artifact = tamil_translation(ctx)
    verdict = verify_deliverable(ctx.ledger, deliverable("text", "ta", produced_by="translate_text"), artifact)
    assert "kind" not in failing(verdict) and "format" not in failing(verdict)
    assert "produced_by" not in failing(verdict)


def test_llm_text_fails_when_bodhan_output_was_planned(ctx: ToolContext) -> None:
    artifact = add(ctx, "transcript.en.md", "# Transcript\n\nTranslated.", "markdown", producer="llm_english",
                   tool="write", language="en")  # fmt: skip
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "en", produced_by="translate_text"), artifact)
    assert failing(verdict)["produced_by"] == "failed"
    assert verdict.status == "failed"


def test_llm_file_cannot_be_indic_deliverable(ctx: ToolContext) -> None:
    artifact = add(ctx, "fake.md", "Plain English.", "markdown", "llm_english", language="ta")
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "ta"), artifact)
    assert failing(verdict)["provenance"] == "failed"


def test_user_input_cannot_be_deliverable(ctx: ToolContext) -> None:
    path = write_new(ctx.settings.work_dir / "given.md", b"Given.")
    artifact = ctx.ledger.add(
        Artifact(id="f1", path=str(path), kind="markdown", producer="user_input", language="en", step_status="ok")
    )
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "en"), artifact)
    assert failing(verdict)["provenance"] == "failed"


@pytest.mark.parametrize(("status", "expected"), [("failed", "failed"), ("pending", "failed"), ("warning", "warning")])
def test_step_status_is_carried(ctx: ToolContext, status: str, expected: str) -> None:
    artifact = add(ctx, "a.en.md", "Hello world.", "markdown", tool="translate_text", language="en", status=status)
    verdict = verify_deliverable(ctx.ledger, deliverable("markdown", "en"), artifact)
    assert failing(verdict)["step_status"] == expected
    assert verdict.status == expected


def test_english_answer_is_verified(ctx: ToolContext) -> None:
    artifact = subtitles(ctx)
    verdict = verify_deliverable(ctx.ledger, deliverable("answer"), None, f"Subtitles are in {artifact.path}.")
    assert verdict.status == "verified"
    assert statuses(verdict) == {"answer_english": "passed", "answer_files": "passed"}


def test_answer_without_paths_has_one_check(ctx: ToolContext) -> None:
    verdict = verify_deliverable(ctx.ledger, deliverable("answer"), None, "The talk covers photosynthesis.")
    assert statuses(verdict) == {"answer_english": "passed"}


def test_answer_mentioning_unknown_file_warns(ctx: ToolContext) -> None:
    verdict = verify_deliverable(ctx.ledger, deliverable("answer"), None, "See /tmp/nowhere/out.md for details.")
    assert failing(verdict) == {"answer_files": "warning"}


@pytest.mark.parametrize("answer", [None, "  ", "काम हो गया।", "sab theek hai aur bahut accha hai yaar"])
def test_bad_answers_fail(ctx: ToolContext, answer: str | None) -> None:
    verdict = verify_deliverable(ctx.ledger, deliverable("answer"), None, answer)
    assert verdict.status == "failed"
    assert failing(verdict)["answer_english"] == "failed"


def test_verdicts_fit_deliverable_report(ctx: ToolContext) -> None:
    cases = [
        (deliverable("markdown", "ta"), tamil_translation(ctx)),
        (deliverable("audio", "ta", format="wav"), speech(ctx)),
        (deliverable("subtitles", "en"), subtitles(ctx)),
        (deliverable("markdown", "en"), None),
    ]
    for planned, artifact in cases:
        verdict = verify_deliverable(ctx.ledger, planned, artifact)
        path = f"artifacts/{Path(artifact.path).name}" if artifact is not None and verdict.status != "missing" else None
        DeliverableReport(
            id=planned.id, kind=planned.kind, status=verdict.status, path=path, label=verdict.label,
            checks=verdict.checks,
        )  # fmt: skip
