"""Every tool's real output (success and failure) matches the output schema the MCP server
advertises, so a third-party client can rely on it."""

import jsonschema
import pytest
from conftest import make_image, make_pdf, speechlike, write_wav

from bodhan_mcp.registry import invoke
from bodhan_mcp.tools import TOOL_BY_NAME, TOOLS
from bodhan_mcp.tools.schemas import output_schema


@pytest.fixture
def files(tmp_path):
    (tmp_path / "en.txt").write_text("The school opens at nine.", encoding="utf-8")
    return {"audio": write_wav(tmp_path / "a.wav", speechlike(6, 16000)), "pdf": make_pdf(tmp_path / "d.pdf", 2),
            "image": make_image(tmp_path / "p.png"), "text": tmp_path / "en.txt"}


def check(name: str, result: dict) -> None:
    jsonschema.validate(result, output_schema(name))


async def test_all_tools_match_their_output_schema(fake, make_ctx, files, tmp_path):
    ctx = make_ctx(fake)
    t, _ = await invoke(TOOL_BY_NAME["transcribe_audio"], ctx, {"path": str(files["audio"]), "language": "hi"})
    tr, _ = await invoke(TOOL_BY_NAME["translate_text"], ctx, {"path": str(files["text"]), "target_language": "hi"})
    sp, _ = await invoke(TOOL_BY_NAME["synthesize_speech"], ctx, {"text": "नमस्ते दुनिया।", "language": "hi", "format": "wav"})
    calls = {
        "inspect_files": {"paths": [str(tmp_path)]},
        "create_subtitles": {"path": t["segments_path"], "subtitle_language": "en"},
        "ocr_document": {"path": str(files["pdf"])},
        "verify_translation": {"translation_path": tr["path"], "source_path": str(files["text"]), "target_language": "hi"},
        "verify_speech": {"audio_path": sp["path"], "language": "hi", "expected_text": "नमस्ते दुनिया।"},
        "detect_language": {"text": "ఇది తెలుగు"},
        "read_text": {"path": str(files["text"])},
        "write_text": {"name": "n.md", "content": "An English note."},
        "list_voices": {"language": "bn"},
    }
    check("transcribe_audio", t)
    check("translate_text", tr)
    check("synthesize_speech", sp)
    for name, a in calls.items():
        result, is_error = await invoke(TOOL_BY_NAME[name], ctx, a)
        assert not is_error, (name, result)
        check(name, result)


async def test_failures_match_the_schema_too(fake, make_ctx):
    ctx = make_ctx(fake)
    for spec in TOOLS:
        result, is_error = await invoke(spec, ctx, {"definitely_not_an_argument": 1})
        assert is_error
        check(spec.name, result)
