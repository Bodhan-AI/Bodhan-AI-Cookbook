"""Unit tests for the Standalone MCP Server tools (src.bodhan_mcp.server)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.bodhan_mcp.server import (
    detect_language,
    extract_document_text,
    get_active_artifacts_dir,
    inspect_media,
    mcp,
    read_text_file,
    reset_active_artifacts_dir,
    resolve_artifact_path,
    set_active_artifacts_dir,
    synthesize_speech,
    transcribe_audio,
    translate_text,
    write_text_file,
)
from tests.test_audio import create_synthetic_wav
from tests.test_document import create_synthetic_docx, create_synthetic_pdf, create_synthetic_tiff


async def test_inspect_media_audio(tmp_path: Path):
    wav_path = create_synthetic_wav(tmp_path / "voice.wav", duration_sec=2.5)
    res_str = await inspect_media(str(wav_path))
    res = json.loads(res_str)
    assert res["type"] == "audio"
    assert res["duration_seconds"] == 2.5
    assert res["channels"] == 1


async def test_inspect_media_pdf(tmp_path: Path):
    pdf_path = create_synthetic_pdf(tmp_path / "doc.pdf", num_pages=5)
    res_str = await inspect_media(str(pdf_path))
    res = json.loads(res_str)
    assert res["type"] == "pdf"
    assert res["page_count"] == 5


async def test_inspect_media_multi_format(tmp_path: Path):
    from PIL import Image

    # 1. DOCX inspection
    docx_path = create_synthetic_docx(tmp_path / "doc.docx")
    docx_res = json.loads(await inspect_media(str(docx_path)))
    assert docx_res["type"] == "document"
    assert docx_res["format"] == "DOCX"
    assert docx_res["page_count"] == 1

    # 2. Image formats: PNG, JPG, WEBP, BMP
    png_path = tmp_path / "test.png"
    Image.new("RGB", (50, 50), color="blue").save(str(png_path))
    png_res = json.loads(await inspect_media(str(png_path)))
    assert png_res["type"] == "image"
    assert png_res["format"] == "png"

    bmp_path = tmp_path / "test.bmp"
    Image.new("RGB", (50, 50), color="green").save(str(bmp_path))
    bmp_res = json.loads(await inspect_media(str(bmp_path)))
    assert bmp_res["type"] == "image"
    assert bmp_res["format"] == "bmp"

    webp_path = tmp_path / "test.webp"
    Image.new("RGB", (50, 50), color="red").save(str(webp_path))
    webp_res = json.loads(await inspect_media(str(webp_path)))
    assert webp_res["type"] == "image"
    assert webp_res["format"] == "webp"

    # 3. Multi-page TIFF
    tiff_path = create_synthetic_tiff(tmp_path / "test.tiff", num_pages=2)
    tiff_res = json.loads(await inspect_media(str(tiff_path)))
    assert tiff_res["type"] in {"document", "image"}


async def test_read_docx_via_read_text_file(tmp_path: Path):
    docx_path = create_synthetic_docx(tmp_path / "read_sample.docx")
    res_str = await read_text_file(str(docx_path), offset=0, limit=50)
    data = json.loads(res_str)
    assert data["file"] == "read_sample.docx"
    assert data["chars_read"] == 50
    assert data["has_more"] is True
    assert "Indic AI Cookbook Overview" in data["content"]

    # Read full file
    res_full_str = await read_text_file(str(docx_path), offset=0, limit=10000)
    data_full = json.loads(res_full_str)
    assert data_full["has_more"] is False
    assert "| Model | Modality |" in data_full["content"]
    assert "नमस्ते भारत!" in data_full["content"]


async def test_extract_document_text_docx(tmp_path: Path):
    docx_path = create_synthetic_docx(tmp_path / "extract_sample.docx")

    # Markdown format
    md_text = await extract_document_text(str(docx_path), table_format="markdown")
    assert "Indic AI Cookbook Overview" in md_text
    assert "| Model | Modality |" in md_text
    assert "| indic-transcribe | ASR |" in md_text

    # HTML format
    html_text = await extract_document_text(str(docx_path), table_format="html")
    assert "Indic AI Cookbook Overview" in html_text
    assert "<table>" in html_text
    assert "<tr><th>Model</th><th>Modality</th></tr>" in html_text
    assert "<tr><td>indic-transcribe</td><td>ASR</td></tr>" in html_text


async def test_unsupported_language_fails_honestly():
    # Attempt translation to French (impossible task)
    with pytest.raises(ToolError) as exc_info:
        await translate_text(text="Hello", target_language="fr")
    assert "Language 'fr' is not supported" in str(exc_info.value)

    # Attempt transcription in unsupported language
    with pytest.raises(ToolError) as exc_info:
        await transcribe_audio(file_path="non_existent.wav", language="fr")
    assert "Audio file not found" in str(exc_info.value) or "Language 'fr' is not supported" in str(exc_info.value)


def test_translation_script_validation():
    """A translation must contain target-script characters; an echo must not pass."""
    from src.bodhan_mcp.server import _translation_script_ok

    # Real Tamil translation passes; an English echo for Tamil fails.
    assert _translation_script_ok("கூட்டம் அடுத்த வாரம் வரை ஒத்திவைக்கப்பட்டுள்ளது.", "ta")
    assert not _translation_script_ok("The meeting has been postponed until next week.", "ta")

    # Real Hindi passes; an English echo for Hindi fails.
    assert _translation_script_ok("आज शहर में मौसम सुहावना है।", "hi")
    assert not _translation_script_ok("The weather is pleasant today in the city.", "hi")

    # English target expects Latin; a Devanagari echo fails.
    assert _translation_script_ok("The weather is pleasant today in the city.", "en")
    assert not _translation_script_ok("आज शहर में मौसम सुहावना है।", "en")

    # Multi-script languages accept any of their native scripts.
    assert _translation_script_ok("ਸਤ ਸ੍ਰੀ ਅਕਾਲ ਜੀ, ਤੁਸੀ ਕਿਵੇਂ ਹੋ?", "pa")
    assert _translation_script_ok("कॉशुर छि अख ख़ूब ख़ान ज़बान।", "ks")   # Devanagari Kashmiri
    assert _translation_script_ok("کأل چھےأ أستھؠ", "ks")                # Arabic-script Kashmiri

    # Short or trivial texts skip validation (avoid false ToolErrors).
    assert _translation_script_ok("OK", "ta")
    assert _translation_script_ok("   ", "bn")


async def test_empty_speech_synthesis_raises():
    with pytest.raises(ToolError) as exc_info:
        await synthesize_speech(text="   ", language="hi")
    assert "Text for speech synthesis cannot be empty" in str(exc_info.value)


async def test_read_and_write_text_file(tmp_path: Path):
    test_file = tmp_path / "artifacts" / "test_output.txt"
    sample_content = "नमस्ते, यह परीक्षण सामग्री है।\nSecond line."

    # Write file
    write_res = await write_text_file(str(test_file), sample_content)
    write_data = json.loads(write_res)
    assert write_data["status"] == "success"
    assert test_file.exists()

    # Read file (full)
    read_res = await read_text_file(str(test_file))
    read_data = json.loads(read_res)
    assert read_data["content"] == sample_content
    assert read_data["total_chars"] == len(sample_content)
    assert read_data["has_more"] is False

    # Read with pagination (offset=0, limit=5)
    paged_res = await read_text_file(str(test_file), offset=0, limit=5)
    paged_data = json.loads(paged_res)
    assert paged_data["chars_read"] == 5
    assert paged_data["content"] == sample_content[:5]
    assert paged_data["has_more"] is True
    assert paged_data["next_offset"] == 5

    # Read next slice (offset=5, limit=100)
    paged_res2 = await read_text_file(str(test_file), offset=5, limit=100)
    paged_data2 = json.loads(paged_res2)
    assert paged_data2["content"] == sample_content[5:]
    assert paged_data2["has_more"] is False

    # Read non-existent file
    with pytest.raises(ToolError) as exc_info:
        await read_text_file(str(tmp_path / "non_existent.txt"))
    assert "File not found" in str(exc_info.value)


async def test_detect_language():
    # Devanagari (Hindi/Marathi)
    hi_res = json.loads(await detect_language("नमस्ते, क्या हाल है?"))
    assert hi_res["script"] == "Devanagari"
    assert "hi" in hi_res["likely_languages"]

    # Tamil
    ta_res = json.loads(await detect_language("வணக்கம்! நீங்கள் எப்படி இருக்கிறீர்கள்?"))
    assert ta_res["script"] == "Tamil"
    assert "ta" in ta_res["likely_languages"]

    # Bengali
    bn_res = json.loads(await detect_language("আমার সোনার বাংলা, আমি তোমায় ভালোবাসি।"))
    assert bn_res["script"] == "Bengali"
    assert "bn" in bn_res["likely_languages"]

    # English / Latin
    en_res = json.loads(await detect_language("This is an authentic English summary paragraph."))
    assert en_res["script"] == "Latin"
    assert "en" in en_res["likely_languages"]

    # Empty / whitespace
    empty_res = json.loads(await detect_language("   "))
    assert empty_res["script"] == "unknown"


async def test_resolve_artifact_path_and_traversal_protection(tmp_path: Path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    token = set_active_artifacts_dir(artifacts_dir)
    try:
        assert get_active_artifacts_dir() == artifacts_dir.resolve()

        # 1. Normal relative path resolves inside active artifacts dir
        p1 = resolve_artifact_path("subtitles.srt")
        assert p1 == artifacts_dir.resolve() / "subtitles.srt"

        # 2. Redundant leading 'artifacts/' or 'artifacts\' prefix is stripped
        p2 = resolve_artifact_path("artifacts/report.md")
        assert p2 == artifacts_dir.resolve() / "report.md"

        p3 = resolve_artifact_path(r"artifacts\nested\output.txt")
        assert p3 == artifacts_dir.resolve() / "nested" / "output.txt"

        p4 = resolve_artifact_path("./artifacts/data.json")
        assert p4 == artifacts_dir.resolve() / "data.json"

        # 3. Path traversal outside active artifacts directory is blocked
        with pytest.raises(ToolError) as exc_info:
            resolve_artifact_path("../secret.txt")
        assert "Path traversal detected" in str(exc_info.value)

        with pytest.raises(ToolError) as exc_info:
            resolve_artifact_path("../../etc/passwd")
        assert "Path traversal detected" in str(exc_info.value)

        with pytest.raises(ToolError) as exc_info:
            resolve_artifact_path("artifacts/../../escaped.txt")
        assert "Path traversal detected" in str(exc_info.value)

        outside_abs = (tmp_path / "outside.txt").resolve()
        with pytest.raises(ToolError) as exc_info:
            resolve_artifact_path(outside_abs)
        assert "Path traversal detected" in str(exc_info.value)

        # 4. write_text_file blocks path traversal
        with pytest.raises(ToolError) as exc_info:
            await write_text_file("../malicious.txt", "evil")
        assert "Path traversal detected" in str(exc_info.value)
        assert not (tmp_path / "malicious.txt").exists()

        # 5. write_text_file writes cleanly inside artifacts dir
        res = await write_text_file("artifacts/clean.txt", "clean content")
        data = json.loads(res)
        assert data["status"] == "success"
        assert (artifacts_dir / "clean.txt").exists()
        assert (artifacts_dir / "clean.txt").read_text(encoding="utf-8") == "clean content"
    finally:
        reset_active_artifacts_dir(token)

    # 6. Standalone mode (no active artifacts dir) allows arbitrary paths
    assert get_active_artifacts_dir() is None
    standalone_path = resolve_artifact_path(str(tmp_path / "standalone.txt"))
    assert standalone_path == (tmp_path / "standalone.txt").resolve()


async def test_mcp_client_session_protocol_and_bad_inputs(tmp_path: Path):
    """Test the standalone MCP server using an official MCP ClientSession over memory streams.
    
    Verifies that off-the-shelf MCP clients can:
    1. Initialize and discover the server.
    2. List all registered tools and their schemas.
    3. Call tools successfully with valid inputs.
    4. Handle ToolError gracefully on invalid/missing inputs (isError=True).
    """
    import anyio
    from mcp import ClientSession

    c2s_send, c2s_recv = anyio.create_memory_object_stream(50)
    s2c_send, s2c_recv = anyio.create_memory_object_stream(50)

    async with anyio.create_task_group() as tg:
        tg.start_soon(mcp._mcp_server.run, c2s_recv, s2c_send, mcp._mcp_server.create_initialization_options())

        async with ClientSession(s2c_recv, c2s_send) as session:
            # 1. Initialize
            init_res = await session.initialize()
            assert init_res.serverInfo.name == "bodhan-mcp"

            # 2. List tools
            tools_res = await session.list_tools()
            tool_names = {t.name for t in tools_res.tools}
            expected_tools = {
                "transcribe_audio",
                "translate_text",
                "synthesize_speech",
                "extract_document_text",
                "inspect_media",
                "read_text_file",
                "write_text_file",
                "detect_language",
            }
            assert expected_tools.issubset(tool_names), f"Missing tools: {expected_tools - tool_names}"

            # 3. Call detect_language (valid input)
            lang_res = await session.call_tool("detect_language", {"text": "यह एक हिंदी वाक्य है।"})
            assert not lang_res.isError
            lang_data = json.loads(lang_res.content[0].text)
            assert lang_data["script"] == "Devanagari"
            assert "hi" in lang_data["likely_languages"]

            # 4. Write and Read text file (valid input)
            test_file = tmp_path / "mcp_client_test.txt"
            write_res = await session.call_tool(
                "write_text_file",
                {"file_path": str(test_file), "content": "Hello from MCP Client!"},
            )
            assert not write_res.isError
            assert test_file.exists()

            read_res = await session.call_tool("read_text_file", {"file_path": str(test_file)})
            assert not read_res.isError
            assert "Hello from MCP Client!" in read_res.content[0].text

            # 5. Bad input handling: missing file in inspect_media -> returns isError=True
            bad_inspect = await session.call_tool("inspect_media", {"file_path": "non_existent_file.mp3"})
            assert bad_inspect.isError
            assert "File not found" in bad_inspect.content[0].text

            # 6. Bad input handling: missing file in read_text_file -> returns isError=True
            bad_read = await session.call_tool("read_text_file", {"file_path": "non_existent_file.txt"})
            assert bad_read.isError
            assert "File not found" in bad_read.content[0].text

            # 7. Bad input handling: unsupported language in translate_text -> returns isError=True
            bad_trans = await session.call_tool(
                "translate_text",
                {"text": "Hello world", "target_language": "klingon_bad_lang"},
            )
            assert bad_trans.isError
            assert "is not supported" in bad_trans.content[0].text

            # 8. Bad input handling: empty text in synthesize_speech -> returns isError=True
            bad_tts = await session.call_tool(
                "synthesize_speech",
                {"text": "   ", "language": "hi"},
            )
            assert bad_tts.isError
            assert "cannot be empty" in bad_tts.content[0].text

            # 9. Verify FastMCP Resources over live stdio session
            resources_list = await session.list_resources()
            resource_uris = {str(r.uri) for r in resources_list.resources}
            assert "bodhan://catalog/languages" in resource_uris
            assert "bodhan://catalog/voices" in resource_uris
            assert "bodhan://guidelines/routing-contracts" in resource_uris

        tg.cancel_scope.cancel()


def test_fastmcp_declarative_resources():
    from src.bodhan_mcp.server import (
        get_languages_resource,
        get_voices_resource,
        get_routing_contracts_resource,
        mcp,
    )

    # 1. Resource registration
    resources = mcp._resource_manager.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "bodhan://catalog/languages" in uris
    assert "bodhan://catalog/voices" in uris
    assert "bodhan://guidelines/routing-contracts" in uris

    # 2. Languages resource content
    lang_data = json.loads(get_languages_resource())
    assert lang_data["count"] == 28
    assert "ks-Deva" in lang_data["script_subtags"]
    assert "sd-Arab" in lang_data["script_subtags"]
    assert "mni-Beng" in lang_data["script_subtags"]

    # 3. Voices resource content
    voice_data = json.loads(get_voices_resource())
    assert voice_data["sample_rate_hz"] == 24000
    assert voice_data["audio_format"] == "WAV"
    assert "hi" in voice_data["default_voices"]
    assert voice_data["voice_cloning"]["supported"] is False

    # 4. Routing contracts resource content
    contracts = json.loads(get_routing_contracts_resource())
    assert "bounded_pagination" in contracts
    assert "offset: 4000, limit: 2000" in contracts["bounded_pagination"]["example"]
    assert "plain_text_vs_ocr" in contracts
    assert "audio_deliverables" in contracts
    assert "voice_cloning_impossible" in contracts["audio_deliverables"]
    assert "audio_verification" in contracts["audio_deliverables"]
    assert "audio_verification_and_inspection" in contracts


async def test_transcribe_audio_srt_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "mock-key")
    wav = create_synthetic_wav(tmp_path / "test_audio.wav", duration_sec=2.5)
    out_srt = tmp_path / "artifacts" / "test_output.srt"

    with patch("src.bodhan_mcp.server._call_transcribe", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = "नमस्ते दुनिया"
        result = await transcribe_audio(
            file_path=str(wav),
            language="hi",
            output_format="srt",
            output_path=str(out_srt),
        )

    meta = json.loads(result)
    assert meta["status"] == "success"
    assert meta["output_format"] == "srt"
    assert meta["cues_count"] == 1
    assert meta["duration_seconds"] > 0
    assert "00:00:00,000" in meta["preview"]
    assert "नमस्ते दुनिया" in meta["preview"]

    assert out_srt.is_file()
    content = out_srt.read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> 00:00:02,500\nनमस्ते दुनिया\n" in content


async def test_transcribe_audio_vtt_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "mock-key")
    wav = create_synthetic_wav(tmp_path / "test_audio_vtt.wav", duration_sec=1.8)
    out_vtt = tmp_path / "artifacts" / "test_output.vtt"

    with patch("src.bodhan_mcp.server._call_transcribe", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = "Hello world"
        result = await transcribe_audio(
            file_path=str(wav),
            output_format="vtt",
            output_path=str(out_vtt),
        )

    meta = json.loads(result)
    assert meta["status"] == "success"
    assert meta["output_format"] == "vtt"
    assert out_vtt.is_file()
    content = out_vtt.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:01.800" in content


async def test_transcribe_audio_inferred_format_from_output_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "mock-key")
    wav = create_synthetic_wav(tmp_path / "inferred.wav", duration_sec=1.0)
    out_path = tmp_path / "subtitles.srt"

    with patch("src.bodhan_mcp.server._call_transcribe", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = "Auto inferred SRT"
        result = await transcribe_audio(
            file_path=str(wav),
            output_path=str(out_path),
        )

    meta = json.loads(result)
    assert meta["output_format"] == "srt"
    assert out_path.is_file()
    assert "00:00:00,000" in out_path.read_text(encoding="utf-8")


async def test_transcribe_audio_text_with_output_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "mock-key")
    wav = create_synthetic_wav(tmp_path / "text_file.wav", duration_sec=1.5)
    out_txt = tmp_path / "transcript.txt"

    with patch("src.bodhan_mcp.server._call_transcribe", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = "Verbatim plain text transcript."
        result = await transcribe_audio(
            file_path=str(wav),
            output_format="text",
            output_path=str(out_txt),
        )

    meta = json.loads(result)
    assert meta["status"] == "success"
    assert meta["output_format"] == "text"
    assert out_txt.is_file()
    assert out_txt.read_text(encoding="utf-8") == "Verbatim plain text transcript."


async def test_transcribe_audio_task_forwarding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "mock-key")
    wav = create_synthetic_wav(tmp_path / "task_fwd.wav", duration_sec=1.0)

    with patch("src.bodhan_mcp.server._call_transcribe", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = "Translated English text"
        result = await transcribe_audio(
            file_path=str(wav),
            task="translate",
        )

    assert result == "Translated English text"
    mock_call.assert_called_once()
    args = mock_call.call_args[0]
    kwargs = mock_call.call_args[1]
    task_arg = args[4] if len(args) > 4 else kwargs.get("task")
    assert task_arg == "translate"


async def test_transcribe_audio_legacy_text_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "mock-key")
    wav = create_synthetic_wav(tmp_path / "legacy.wav", duration_sec=1.2)

    with patch("src.bodhan_mcp.server._call_transcribe", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = "Legacy transcript string."
        result = await transcribe_audio(file_path=str(wav))

    assert result == "Legacy transcript string."


