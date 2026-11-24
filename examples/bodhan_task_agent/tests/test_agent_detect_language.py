from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bodhan_agent.core import guards
from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.ledger import Artifact, Ledger
from bodhan_agent.tools import detect_language as module
from bodhan_agent.tools.base import LocalToolError, ToolContext, register, write_new
from bodhan_agent.tools.detect_language import DetectLanguageTool

HINDI = "यह एक किताब है। इसमें बहुत सारी कहानियाँ हैं और बच्चे इसे पढ़ते हैं। यह किताब मेरी है।"
MARATHI = "हे एक पुस्तक आहे. यात खूप गोष्टी आहेत आणि मुले ते वाचतात. हे पुस्तक माझे आहे."
NEPALI = "यो एउटा किताब हो। यसमा धेरै कथाहरू छन् र केटाकेटीले यो पढ्छन्। यो किताब मेरो हो।"
BENGALI = "এটি একটি বই। এতে অনেক গল্প আছে এবং শিশুরা এটি পড়ে। এই বইটি আমার।"
ASSAMESE = "এইখন এখন কিতাপ। ইয়াত বহুত সাধু আছে আৰু ল'ৰা-ছোৱালীয়ে এইখন পঢ়ে। এই কিতাপখন মোৰ।"
URDU = "یہ ایک کتাب ہے۔ اس میں بہت سی کہانیاں ہیں اور بچے اسے پڑھتے ہیں۔"
SINDHI = "هي ڪتاب آهي ۽ هن ۾ ڪهاڻيون آهن. ٻار هن کي پڙهن ٿا."


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    settings.work_dir.mkdir(parents=True)
    return ToolContext(settings=settings, ledger=Ledger())


def add(ctx: ToolContext, name: str, data: str | bytes, status: str = "ok", kind: str = "text") -> Artifact:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    artifact = register(ctx, write_new(ctx.settings.work_dir / name, raw), kind, "bodhan", tool="ocr_document")
    ctx.ledger.set_status(artifact.id, status)
    return artifact


async def detect(ctx: ToolContext, text: str, name: str = "a.txt") -> dict[str, Any]:
    artifact = add(ctx, name, text)
    return (await DetectLanguageTool().execute({"path": artifact.path}, ctx)).data


async def detect_error(ctx: ToolContext, path: str) -> LocalToolError:
    with pytest.raises(LocalToolError) as error:
        await DetectLanguageTool().execute({"path": path}, ctx)
    return error.value


def test_definition_is_named_detect_language_and_has_reason() -> None:
    definition = DetectLanguageTool().definition()
    assert definition["function"]["name"] == "detect_language"
    assert definition["function"]["parameters"]["required"] == ["reason", "path"]


@pytest.mark.parametrize(
    ("text", "language", "script"),
    [
        (HINDI, "hi", "Devanagari"),
        (MARATHI, "mr", "Devanagari"),
        (NEPALI, "ne", "Devanagari"),
        (BENGALI, "bn", "Bengali"),
        (ASSAMESE, "as", "Bengali"),
        (URDU, "ur", "Arabic"),
        (SINDHI, "sd", "Arabic"),
    ],
)
async def test_shared_scripts_resolved_by_markers(ctx: ToolContext, text: str, language: str, script: str) -> None:
    data = await detect(ctx, text)
    assert (data["language"], data["confidence"], data["script"]) == (language, "high", script)
    assert data["candidates"][0]["language"] == language
    assert f"{script} is shared by" in data["note"]


async def test_hindi_result_shape(ctx: ToolContext) -> None:
    data = await detect(ctx, HINDI)
    assert data["artifact"] == "A1"
    assert data["scripts"] == [{"script": "Devanagari", "share": 1.0}]
    assert data["letters"] > 0
    assert data["candidates"] == [{"language": "hi", "name": "Hindi", "marker_words": 7, "marker_letters": 0}]
    assert "No evidence for" in data["note"] and "mr" in data["note"]


async def test_assamese_letters_beat_bengali(ctx: ToolContext) -> None:
    data = await detect(ctx, ASSAMESE)
    first, second = data["candidates"][:2]
    assert first["language"] == "as" and first["marker_letters"] > 0
    assert second["language"] == "bn"


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("இது ஒரு புத்தகம்.", "ta"),
        ("ఇది ఒక పుస్తకం.", "te"),
        ("ಇದು ಒಂದು ಪುಸ್ತಕ.", "kn"),
        ("ഇത് ഒരു പുസ്തകമാണ്.", "ml"),
        ("આ એક પુસ્તક છે.", "gu"),
        ("ਇਹ ਇੱਕ ਕਿਤਾਬ ਹੈ।", "pa"),
        ("ଏହା ଏକ ବହି।", "or"),
    ],
)
async def test_single_language_scripts_are_high(ctx: ToolContext, text: str, language: str) -> None:
    data = await detect(ctx, text)
    assert (data["language"], data["confidence"]) == (language, "high")
    assert [item["language"] for item in data["candidates"]] == [language]
    assert "note" not in data


async def test_english_is_high(ctx: ToolContext) -> None:
    data = await detect(ctx, "This is a book about plants and how they grow.")
    assert (data["language"], data["confidence"], data["script"]) == ("en", "high", "Latin")
    assert "note" not in data


async def test_romanized_indic_lowers_english_confidence(ctx: ToolContext) -> None:
    text = "aap kaise hain, main theek hoon, bahut accha hai"
    assert guards.romanized_indic_words(text)
    data = await detect(ctx, text)
    assert (data["language"], data["confidence"]) == ("en", "medium")
    assert "romanized" in data["note"]


async def test_no_markers_gives_no_language(ctx: ToolContext) -> None:
    data = await detect(ctx, "किताब")
    assert (data["language"], data["confidence"], data["candidates"]) == (None, "low", [])
    assert "assume a language" in data["note"]


async def test_tie_gives_no_language(ctx: ToolContext) -> None:
    data = await detect(ctx, "এই কিন্তু")
    assert {item["language"] for item in data["candidates"]} == {"as", "bn"}
    assert (data["language"], data["confidence"]) == (None, "low")


async def test_small_lead_is_medium(ctx: ToolContext) -> None:
    data = await detect(ctx, "यह है")
    assert (data["language"], data["confidence"]) == ("hi", "medium")


async def test_weak_lead_over_runner_is_medium(ctx: ToolContext) -> None:
    data = await detect(ctx, "है है है आहे आहे")
    assert [item["language"] for item in data["candidates"]] == ["hi", "mr"]
    assert (data["language"], data["confidence"]) == ("hi", "medium")


async def test_danda_and_punctuation_are_stripped(ctx: ToolContext) -> None:
    data = await detect(ctx, "है। (है) है, «है»")
    assert data["candidates"][0]["marker_words"] == 4


async def test_mixed_scripts_lower_confidence(ctx: ToolContext) -> None:
    data = await detect(ctx, f"This short English introduction comes first. {HINDI}")
    assert data["language"] == "hi"
    assert data["confidence"] == "medium"
    assert [item["script"] for item in data["scripts"]] == ["Devanagari", "Latin"]
    assert "Mixed scripts" in data["note"]


async def test_mostly_english_with_some_indic(ctx: ToolContext) -> None:
    text = "The report covers many topics in plain English words today " * 3 + "नमस्ते"
    data = await detect(ctx, text)
    assert (data["language"], data["confidence"]) == ("en", "high")
    assert data["scripts"][1]["script"] == "Devanagari"


async def test_no_letters(ctx: ToolContext) -> None:
    data = await detect(ctx, "12345 ... !!! ٣٤٥")
    assert data == {
        "artifact": "A1",
        "path": data["path"],
        "letters": 0,
        "language": None,
        "confidence": "none",
        "scripts": [],
        "candidates": [],
        "note": "No letters found.",
    }


async def test_output_never_contains_indic_text(ctx: ToolContext) -> None:
    for index, text in enumerate((HINDI, BENGALI, ASSAMESE, URDU, SINDHI, "किताब")):
        data = await detect(ctx, text, name=f"f{index}.txt")
        assert not guards.contains_indic(json.dumps(data, ensure_ascii=False))


async def test_only_samples_start_of_long_file(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "SAMPLE_CHARS", 20)
    data = await detect(ctx, "English words only here " + HINDI)
    assert (data["language"], data["script"]) == ("en", "Latin")


async def test_utf8_bom_is_ignored(ctx: ToolContext) -> None:
    artifact = add(ctx, "bom.txt", "﻿".encode() + HINDI.encode("utf-8"))
    data = (await DetectLanguageTool().execute({"path": artifact.path}, ctx)).data
    assert data["language"] == "hi"


async def test_result_lists_artifact(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.txt", HINDI)
    result = await DetectLanguageTool().execute({"path": artifact.path}, ctx)
    assert result.artifact_ids == [artifact.id]


async def test_does_not_change_ledger(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.txt", HINDI)
    before = artifact.model_dump()
    await DetectLanguageTool().execute({"path": artifact.path}, ctx)
    assert ctx.ledger.get(artifact.id).model_dump() == before
    assert list(ctx.ledger.artifacts) == [artifact.id]


async def test_rejects_non_text_kind(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.wav", b"RIFF", kind="audio")
    error = await detect_error(ctx, artifact.path)
    assert error.code == "unsupported_input"


async def test_rejects_unknown_file(ctx: ToolContext, tmp_path: Path) -> None:
    stray = tmp_path / "stray.txt"
    stray.write_text(HINDI, encoding="utf-8")
    error = await detect_error(ctx, str(stray))
    assert error.code == "path_not_allowed"


async def test_rejects_relative_path(ctx: ToolContext) -> None:
    error = await detect_error(ctx, "a.txt")
    assert error.code == "invalid_input"


async def test_rejects_failed_artifact(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.txt", HINDI, status="failed")
    error = await detect_error(ctx, artifact.path)
    assert error.code == "artifact_not_usable"


async def test_rejects_non_utf8(ctx: ToolContext) -> None:
    artifact = add(ctx, "a.txt", b"\xff\xfe\x00bad")
    error = await detect_error(ctx, artifact.path)
    assert error.code == "unsupported_encoding"
