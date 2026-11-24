
import asyncio
import io
import sys
import wave
from contextlib import contextmanager
from pathlib import Path

import fitz
import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO / "examples" / "bodhan_tool_agent"
sys.path.insert(0, str(PACKAGE_ROOT))

import bodhan_mcp.server as server_module  # noqa: E402
import bodhan_tool_agent.audio as audio_module  # noqa: E402
from bodhan_mcp.server import create_server  # noqa: E402
from bodhan_tool_agent.audio import (  # noqa: E402
    AudioChunk,
    AudioProcessingError,
    audio_duration_seconds,
    chunk_ranges,
)
from bodhan_tool_agent.bodhan_client import (  # noqa: E402
    DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS,
    BodhanAPIError,
    TranslationChunkError,
    TranslationTruncatedError,
)
from mcp import Client  # noqa: E402


def make_test_wav(
    *,
    channels: int = 1,
    sample_width: int = 2,
    sample_rate: int = 24000,
    frames: int = 240,
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(
            b"\x00" * frames * channels * sample_width
        )
    return buffer.getvalue()


class MockBodhanClient:
    def __init__(self):
        self.calls = []
        self.transcribe_result = "नमस्ते"
        self.translate_result = {
            "translation": "வணக்கம்",
            "target_language": "ta",
            "script": "native",
            "truncated": False,
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        }
        self.speak_result = make_test_wav()
        self.ocr_result = {
            "content": "सूचना",
            "blocks": [
                {
                    "order": 0,
                    "label": "Paragraph",
                    "type": "Text",
                    "bbox_xyxy": [1, 2, 30, 40],
                    "conf": 0.9,
                    "text": "सूचना",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
        self.error = None

    def _maybe_raise(self):
        if self.error is not None:
            raise self.error

    def transcribe(self, file_path, language=None):
        self._maybe_raise()
        self.calls.append(("transcribe", Path(file_path), language))
        return self.transcribe_result

    def translate(
        self,
        text,
        target_language,
        *,
        script=None,
        max_output_tokens=None,
    ):
        self._maybe_raise()
        self.calls.append(
            (
                "translate",
                text,
                target_language,
                script,
                max_output_tokens,
            )
        )
        return self.translate_result

    def speak(self, input_text, voice, lang):
        self._maybe_raise()
        self.calls.append(("speak", input_text, voice, lang))
        return self.speak_result

    def ocr(self, image_path, *, table_format=None, max_tokens=None):
        self._maybe_raise()
        self.calls.append(
            ("ocr", Path(image_path), table_format, max_tokens)
        )
        return self.ocr_result


def run_tool(server, name, arguments):
    async def call():
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(call())


def tool_error_text(result):
    return "\n".join(
        item.text for item in result.content if hasattr(item, "text")
    )


@pytest.fixture(autouse=True)
def mock_short_audio(monkeypatch):
    @contextmanager
    def short_chunks(path, *, temp_root=None):
        resolved = Path(path)
        duration = 10.0
        yield [AudioChunk(resolved, duration, 0.0, duration)]

    monkeypatch.setattr(
        server_module,
        "audio_duration_seconds",
        lambda path: 10.0,
    )
    monkeypatch.setattr(
        server_module,
        "chunk_audio_file",
        short_chunks,
    )


def test_all_tools_register_and_succeed(tmp_path):
    client = MockBodhanClient()
    outputs = tmp_path / "outputs"
    server = create_server(client, output_dir=outputs)

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"synthetic audio")

    image = tmp_path / "page.png"
    image.write_bytes(b"synthetic image")

    async def inspect_tool_names():
        async with Client(server) as mcp_client:
            result = await mcp_client.list_tools()
            return {tool.name for tool in result.tools}

    assert asyncio.run(inspect_tool_names()) == {
        "indic_transcribe",
        "indic_translate",
        "indic_speak",
        "indic_ocr",
    }

    transcribed = run_tool(
        server,
        "indic_transcribe",
        {"file_path": str(audio), "language": "hi"},
    )
    assert transcribed.structured_content["text"] == "नमस्ते"
    assert transcribed.structured_content["language"] == "hi"

    translated = run_tool(
        server,
        "indic_translate",
        {
            "text": "Hello",
            "target_language": "ta",
            "script": "native",
            "max_output_tokens": 321,
        },
    )
    assert translated.structured_content["translation"] == "வணக்கம்"
    assert translated.structured_content["usage"]["total_tokens"] == 5
    assert translated.structured_content["truncated"] is False

    translation_call = next(
        call for call in client.calls if call[0] == "translate"
    )
    assert translation_call == (
        "translate",
        "Hello",
        "ta",
        "native",
        321,
    )

    translated_default = run_tool(
        server,
        "indic_translate",
        {
            "text": "Hello",
            "target_language": "ta",
        },
    )
    default_call = [
        call for call in client.calls
        if call[0] == "translate" and call[1] == "Hello"
    ][-1]
    assert translated_default.structured_content["translation"] == "வணக்கம்"
    assert default_call[-1] == DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS

    spoken = run_tool(
        server,
        "indic_speak",
        {
            "text": "வணக்கம்",
            "language": "ta",
            "voice": "Anitha",
            "output_path": "notice.wav",
        },
    )
    output_path = Path(spoken.structured_content["output_path"])

    assert output_path == outputs.resolve() / "notice.wav"
    assert output_path.read_bytes() == client.speak_result
    assert spoken.structured_content["size_bytes"] == len(client.speak_result)
    assert "audio_bytes" not in spoken.structured_content

    ocr = run_tool(
        server,
        "indic_ocr",
        {
            "image_path": str(image),
            "table_format": "markdown",
            "max_tokens": 2048,
        },
    )
    assert ocr.structured_content["text"] == "सूचना"
    assert ocr.structured_content["blocks"] == client.ocr_result["blocks"]
    assert ocr.structured_content["usage"]["total_tokens"] == 15

    assert all(
        call[0] in {"transcribe", "translate", "speak", "ocr"}
        for call in client.calls
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        ("indic_transcribe", {"file_path": "missing.wav"}, "does not exist"),
        ("indic_transcribe", {"file_path": "."}, "must point to a file"),
        ("indic_transcribe", {"file_path": "notes.txt"}, "Unsupported audio"),
        ("indic_ocr", {"image_path": "missing.png"}, "does not exist"),
        ("indic_ocr", {"image_path": "."}, "must point to a file"),
    ],
)
def test_invalid_input_paths_are_actionable(
    tmp_path,
    monkeypatch,
    tool_name,
    arguments,
    expected,
):
    monkeypatch.chdir(tmp_path)
    server = create_server(
        MockBodhanClient(),
        output_dir=tmp_path / "outputs",
    )

    result = run_tool(server, tool_name, arguments)

    assert result.is_error
    assert expected.lower() in tool_error_text(result).lower()


def test_transcribe_rejects_directory_before_client_call(tmp_path):
    client = MockBodhanClient()
    directory = tmp_path / "audio.wav"
    directory.mkdir()

    server = create_server(client, output_dir=tmp_path / "outputs")

    result = run_tool(
        server,
        "indic_transcribe",
        {"file_path": str(directory)},
    )

    assert result.is_error
    assert "must point to a file" in tool_error_text(result)
    assert client.calls == []


def test_tool_returns_safe_bodhan_api_error(tmp_path):
    client = MockBodhanClient()
    client.error = BodhanAPIError(
        401,
        "secret-like upstream message",
        "invalid_api_key_error",
        "req-42",
    )

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"synthetic audio")

    server = create_server(client, output_dir=tmp_path / "outputs")

    result = run_tool(
        server,
        "indic_transcribe",
        {"file_path": str(audio)},
    )

    message = tool_error_text(result)

    assert result.is_error
    assert "HTTP 401" in message
    assert "invalid_api_key_error" in message
    assert "req-42" in message
    assert "secret-like upstream message" not in message


def test_truncated_translation_is_marked_incomplete(tmp_path):
    client = MockBodhanClient()
    client.error = TranslationTruncatedError(
        {
            "translation": "partially translated",
            "truncated": True,
            "usage": {"total_tokens": 12},
        }
    )

    server = create_server(client, output_dir=tmp_path / "outputs")

    result = run_tool(
        server,
        "indic_translate",
        {"text": "Long source", "target_language": "hi"},
    )

    assert not result.is_error
    assert result.structured_content["status"] == "truncated"
    assert result.structured_content["partial"] is True
    assert result.structured_content["truncated"] is True
    assert result.structured_content["translation"] == ""
    assert result.structured_content["partial_translation"] == "partially translated"


def test_long_translation_mock_reports_chunk_metadata(tmp_path):
    client = MockBodhanClient()
    client.translate_result = {
        "translation": "पहला दूसरा",
        "target_language": "hi",
        "script": "roman",
        "truncated": False,
        "partial": False,
        "chunk_count": 2,
        "chunk_character_counts": [4000, 120],
        "chunk_usage": [
            {"completion_tokens": 100},
            {"completion_tokens": 20},
        ],
        "usage": {"completion_tokens": 120},
    }
    server = create_server(client, output_dir=tmp_path / "outputs")
    source = "word " * 1000

    result = run_tool(
        server,
        "indic_translate",
        {
            "text": source,
            "target_language": "hi",
            "script": "roman",
        },
    )

    assert not result.is_error
    assert result.structured_content["chunk_count"] == 2
    assert result.structured_content["chunk_character_counts"] == [4000, 120]
    assert len(result.structured_content["chunk_usage"]) == 2


def test_failed_middle_translation_reports_partial_completion(tmp_path):
    client = MockBodhanClient()
    client.error = TranslationChunkError(
        2,
        3,
        "पहला भाग",
        1,
        [{"completion_tokens": 10}],
        BodhanAPIError(503, "temporary"),
    )
    server = create_server(client, output_dir=tmp_path / "outputs")

    result = run_tool(
        server,
        "indic_translate",
        {"text": "A" * 5000, "target_language": "hi"},
    )

    assert not result.is_error
    assert result.structured_content["status"] == "failed"
    assert result.structured_content["partial"] is True
    assert result.structured_content["failed_chunk_index"] == 2
    assert result.structured_content["completed_chunk_count"] == 1
    assert result.structured_content["chunk_count"] == 3
    assert result.structured_content["translation"] == ""
    assert result.structured_content["usage"] == {"completion_tokens": 10}


def test_malformed_translation_response_does_not_leak_value(tmp_path):
    client = MockBodhanClient()
    client.translate_result = {
        "translation": 987654321,
        "target_language": "hi",
        "script": "native",
        "truncated": False,
        "usage": {},
    }

    server = create_server(client, output_dir=tmp_path / "outputs")

    result = run_tool(
        server,
        "indic_translate",
        {"text": "Hello", "target_language": "hi"},
    )

    message = tool_error_text(result)

    assert result.is_error
    assert "invalid response" in message.lower()
    assert "987654321" not in message


def test_ocr_works_without_usage(tmp_path):
    client = MockBodhanClient()
    client.ocr_result = {
        "content": "पाठ",
        "blocks": [{"order": 0, "text": "पाठ"}],
    }
    image = tmp_path / "page.png"
    image.write_bytes(b"synthetic image")
    server = create_server(client, output_dir=tmp_path / "outputs")

    result = run_tool(
        server,
        "indic_ocr",
        {"image_path": str(image)},
    )

    assert not result.is_error
    assert result.structured_content["text"] == "पाठ"
    assert result.structured_content["blocks"] == client.ocr_result["blocks"]
    assert result.structured_content["usage"] == {}


def make_pdf(path: Path, page_count: int) -> None:
    document = fitz.open()
    for index in range(page_count):
        page = document.new_page(width=72, height=72)
        page.insert_text((10, 20), f"Page {index + 1}")
    document.save(str(path))
    document.close()


def test_indic_ocr_handles_single_and_multipage_pdf(tmp_path):
    client = MockBodhanClient()
    def single_ocr(image_path, *, table_format=None, max_tokens=None):
        client.calls.append(("ocr", Path(image_path), table_format, max_tokens))
        return {"content": "Page One", "blocks": [], "usage": {"completion_tokens": 5}}

    client.ocr = single_ocr
    server = create_server(client, output_dir=tmp_path / "outputs")

    single = tmp_path / "single.pdf"
    make_pdf(single, 1)
    single_result = run_tool(
        server,
        "indic_ocr",
        {"image_path": str(single)},
    )

    assert not single_result.is_error
    assert single_result.structured_content["text"] == "Page One"
    assert single_result.structured_content["total_pages"] == 1
    assert single_result.structured_content["completed_pages"] == 1

    multi = tmp_path / "multi.pdf"
    make_pdf(multi, 2)
    responses = iter(["Page One", "Page Two"])

    def multi_ocr(image_path, *, table_format=None, max_tokens=None):
        client.calls.append(("ocr", Path(image_path), table_format, max_tokens))
        return {
            "content": next(responses),
            "blocks": [],
            "usage": {"completion_tokens": 5},
        }

    client.ocr = multi_ocr
    multi_result = run_tool(
        server,
        "indic_ocr",
        {"image_path": str(multi)},
    )

    assert not multi_result.is_error
    assert multi_result.structured_content["text"] == "Page One\n\nPage Two"
    assert multi_result.structured_content["page_text"] == [
        "Page One",
        "Page Two",
    ]
    assert multi_result.structured_content["completed_pages"] == 2


def test_pdf_middle_page_failure_is_partial(tmp_path):
    pdf = tmp_path / "three.pdf"
    make_pdf(pdf, 3)
    calls = 0

    def ocr(image_path, *, table_format=None, max_tokens=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BodhanAPIError(503, "temporary")
        return {
            "content": f"Page {calls}",
            "blocks": [],
            "usage": {"completion_tokens": 5},
        }

    client = MockBodhanClient()
    client.ocr = ocr
    server = create_server(client, output_dir=tmp_path / "outputs")

    result = run_tool(
        server,
        "indic_ocr",
        {"image_path": str(pdf)},
    )

    assert not result.is_error
    assert result.structured_content["status"] == "failed"
    assert result.structured_content["partial"] is True
    assert result.structured_content["failed_page"] == 2
    assert result.structured_content["completed_pages"] == 1
    assert result.structured_content["total_pages"] == 3
    assert result.structured_content["page_text"] == ["Page 1"]
    assert calls == 2


def test_empty_corrupt_and_page_limit_pdfs_are_rejected(tmp_path):
    from bodhan_tool_agent.pdf import (
        MAX_PDF_PAGES,
        PDFProcessingError,
        pdf_page_count,
    )

    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf")

    with pytest.raises(PDFProcessingError, match="empty"):
        pdf_page_count(empty)
    with pytest.raises(PDFProcessingError, match="corrupt|unreadable"):
        pdf_page_count(corrupt)

    too_many = tmp_path / "too-many.pdf"
    document = fitz.open()
    for _ in range(MAX_PDF_PAGES + 1):
        document.new_page(width=72, height=72)
    document.save(str(too_many))
    document.close()

    with pytest.raises(PDFProcessingError, match="page limit"):
        pdf_page_count(too_many)


def test_encrypted_pdf_is_rejected(tmp_path):
    from bodhan_tool_agent.pdf import PDFProcessingError, pdf_page_count

    encrypted = tmp_path / "encrypted.pdf"
    document = fitz.open()
    document.new_page(width=72, height=72)
    document.save(
        str(encrypted),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="secret",
    )
    document.close()

    with pytest.raises(PDFProcessingError, match="Encrypted|password"):
        pdf_page_count(encrypted)


def test_pdf_page_limit_is_configurable(tmp_path, monkeypatch):
    from bodhan_tool_agent.pdf import PDFProcessingError, pdf_page_count

    pdf = tmp_path / "two-pages.pdf"
    make_pdf(pdf, 2)
    monkeypatch.setenv("BODHAN_MAX_PDF_PAGES", "1")

    with pytest.raises(PDFProcessingError, match="page limit"):
        pdf_page_count(pdf)



def test_pdf_rendering_cleans_temporary_pages(tmp_path, monkeypatch):
    pdf = tmp_path / "cleanup.pdf"
    make_pdf(pdf, 2)
    temp_parent = tmp_path / "pdf-temp"

    from bodhan_tool_agent.pdf import render_pdf_pages

    with render_pdf_pages(pdf, temp_root=temp_parent) as pages:
        created = [page.path for page in pages]
        assert len(created) == 2
        assert all(path.exists() for path in created)

    assert all(not path.exists() for path in created)


def test_indic_speak_rejects_unsupported_output_format_before_client_call(
    tmp_path,
):
    client = MockBodhanClient()
    outputs = tmp_path / "outputs"
    server = create_server(client, output_dir=outputs)

    result = run_tool(
        server,
        "indic_speak",
        {
            "text": "नमस्ते",
            "language": "hi",
            "voice": "Kavya",
            "output_path": "speech.mp3",
        },
    )

    assert result.is_error
    assert ".wav extension" in tool_error_text(result)
    assert not any(call[0] == "speak" for call in client.calls)
    assert not (outputs / "speech.mp3").exists()

def test_indic_speak_rejects_oversized_text_before_client_call(tmp_path):
    client = MockBodhanClient()
    outputs = tmp_path / "outputs"
    server = create_server(client, output_dir=outputs)

    result = run_tool(
        server,
        "indic_speak",
        {
            "text": "अ" * 2001,
            "language": "hi",
            "voice": "Kavya",
            "output_path": "oversized.wav",
        },
    )

    assert result.is_error
    assert "2000 characters" in tool_error_text(result)
    assert not any(call[0] == "speak" for call in client.calls)
    assert not (outputs / "oversized.wav").exists()


@pytest.mark.parametrize(
    ("audio_bytes", "expected"),
    [
        (b"", "empty or invalid audio data"),
        (b"not a wav", "malformed WAV audio"),
        (make_test_wav(channels=2), "mono"),
        (make_test_wav(sample_width=1), "16-bit"),
        (make_test_wav(sample_rate=16000), "24000 Hz"),
        (make_test_wav(frames=0), "no audio frames"),
    ],
    ids=[
        "empty",
        "not-wav",
        "stereo",
        "wrong-sample-width",
        "wrong-sample-rate",
        "no-frames",
    ],
)
def test_indic_speak_rejects_invalid_wav_without_writing_output(
    tmp_path, audio_bytes, expected
):
    client = MockBodhanClient()
    client.speak_result = audio_bytes
    outputs = tmp_path / "outputs"
    server = create_server(client, output_dir=outputs)

    result = run_tool(
        server,
        "indic_speak",
        {
            "text": "नमस्ते",
            "language": "hi",
            "voice": "Kavya",
            "output_path": "invalid.wav",
        },
    )

    assert result.is_error
    assert expected.lower() in tool_error_text(result).lower()
    assert not (outputs / "invalid.wav").exists()


def test_indic_speak_writes_valid_wav_with_expected_format(tmp_path):
    client = MockBodhanClient()
    outputs = tmp_path / "outputs"
    server = create_server(client, output_dir=outputs)

    result = run_tool(
        server,
        "indic_speak",
        {
            "text": "नमस्ते",
            "language": "hi",
            "voice": "Kavya",
            "output_path": "valid.wav",
        },
    )

    assert not result.is_error
    output_path = outputs.resolve() / "valid.wav"
    assert output_path.exists()
    assert output_path.read_bytes() == client.speak_result
    assert result.structured_content["size_bytes"] == output_path.stat().st_size
    with wave.open(str(output_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 24000
        assert wav_file.getnframes() > 0


def test_speak_rejects_output_traversal_and_existing_file(tmp_path):
    client = MockBodhanClient()
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    existing = outputs / "existing.wav"
    existing.write_bytes(b"preserve")

    server = create_server(client, output_dir=outputs)
    args = {
        "text": "नमस्ते",
        "language": "hi",
        "voice": "Kavya",
    }

    traversal = run_tool(
        server,
        "indic_speak",
        {**args, "output_path": "..\\outside.wav"},
    )
    overwrite = run_tool(
        server,
        "indic_speak",
        {**args, "output_path": "existing.wav"},
    )

    assert traversal.is_error
    assert "inside the recipe outputs directory" in tool_error_text(traversal)
    assert overwrite.is_error
    assert "already exists" in tool_error_text(overwrite)
    assert existing.read_bytes() == b"preserve"


def test_output_path_must_not_overwrite_input_file(tmp_path):
    client = MockBodhanClient()
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    input_file = outputs / "input.wav"
    input_file.write_bytes(b"source")

    server = create_server(client, output_dir=outputs)

    result = run_tool(
        server,
        "indic_speak",
        {
            "text": "नमस्ते",
            "language": "hi",
            "voice": "Kavya",
            "output_path": "input.wav",
        },
    )

    assert result.is_error
    assert "already exists" in tool_error_text(result)
    assert input_file.read_bytes() == b"source"


def test_audio_chunk_ranges_cover_short_exact_and_long_boundaries():
    assert chunk_ranges(10.0) == [(0.0, 10.0)]
    assert chunk_ranges(30.0) == [(0.0, 30.0)]
    assert chunk_ranges(30.1) == [(0.0, 25.0), (25.0, 30.1)]
    assert chunk_ranges(60.0) == [(0.0, 25.0), (25.0, 50.0), (50.0, 60.0)]


def test_chunked_transcription_preserves_order(monkeypatch, tmp_path):
    client = MockBodhanClient()
    client.transcribe_result = None
    responses = iter(["first", "second", "third"])

    def transcribe(file_path, language=None):
        client.calls.append(("transcribe", Path(file_path), language))
        return next(responses)

    client.transcribe = transcribe
    audio = tmp_path / "long.wav"
    audio.write_bytes(b"synthetic")

    @contextmanager
    def chunks(path, *, temp_root=None):
        yield [
            AudioChunk(tmp_path / "chunk-1.wav", 25.0, 0.0, 25.0),
            AudioChunk(tmp_path / "chunk-2.wav", 25.0, 25.0, 50.0),
            AudioChunk(tmp_path / "chunk-3.wav", 10.0, 50.0, 60.0),
        ]

    monkeypatch.setattr(server_module, "audio_duration_seconds", lambda path: 60.0)
    monkeypatch.setattr(server_module, "chunk_audio_file", chunks)

    server = create_server(client, output_dir=tmp_path / "outputs")
    result = run_tool(
        server,
        "indic_transcribe",
        {"file_path": str(audio), "language": "hi"},
    )

    assert not result.is_error
    assert result.structured_content["text"] == "first second third"
    assert result.structured_content["chunk_count"] == 3
    assert result.structured_content["chunk_durations_seconds"] == [25.0, 25.0, 10.0]
    assert [call[2] for call in client.calls] == ["hi", "hi", "hi"]


def test_failed_middle_chunk_returns_error(monkeypatch, tmp_path):
    audio = tmp_path / "long.wav"
    audio.write_bytes(b"synthetic")
    temp_paths: list[Path] = []

    @contextmanager
    def chunks(path, *, temp_root=None):
        chunk_paths = [tmp_path / f"chunk-{index}.wav" for index in range(3)]
        temp_paths.extend(chunk_paths)
        for chunk_path in chunk_paths:
            chunk_path.write_bytes(b"temporary")
        try:
            yield [
                AudioChunk(chunk_paths[0], 25.0, 0.0, 25.0),
                AudioChunk(chunk_paths[1], 25.0, 25.0, 50.0),
                AudioChunk(chunk_paths[2], 10.0, 50.0, 60.0),
            ]
        finally:
            for chunk_path in chunk_paths:
                chunk_path.unlink(missing_ok=True)

    calls = 0

    def transcribe(file_path, language=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BodhanAPIError(503, "temporary failure")
        return f"part-{calls}"

    client = MockBodhanClient()
    client.transcribe = transcribe
    monkeypatch.setattr(server_module, "audio_duration_seconds", lambda path: 60.0)
    monkeypatch.setattr(server_module, "chunk_audio_file", chunks)

    server = create_server(client, output_dir=tmp_path / "outputs")
    result = run_tool(
        server,
        "indic_transcribe",
        {"file_path": str(audio), "language": "ta"},
    )

    assert result.is_error
    assert "HTTP 503" in tool_error_text(result)
    assert all(not path.exists() for path in temp_paths)


def test_chunk_audio_file_cleans_temporary_files(monkeypatch, tmp_path):
    source = tmp_path / "long.wav"
    source.write_bytes(b"wav")

    class FakeSegment:
        def __init__(self, duration_ms):
            self.duration_ms = duration_ms

        def __len__(self):
            return self.duration_ms

        def __getitem__(self, key):
            start = key.start or 0
            stop = key.stop
            return FakeSegment(max(0, stop - start))

        def export(self, path, format):
            Path(path).write_bytes(b"chunk")
            return path

    monkeypatch.setattr(
        audio_module,
        "audio_duration_seconds",
        lambda path: 60.0,
    )
    monkeypatch.setattr(
        audio_module.AudioSegment,
        "from_file",
        lambda *args, **kwargs: FakeSegment(60000),
    )

    created_paths: list[Path] = []
    with audio_module.chunk_audio_file(source, temp_root=tmp_path) as chunks:
        created_paths = [chunk.path for chunk in chunks]
        assert len(chunks) == 3
        assert all(path.exists() for path in created_paths)

    assert all(not path.exists() for path in created_paths)


def test_empty_and_unsupported_audio_are_rejected(tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    unsupported = tmp_path / "document.txt"
    unsupported.write_bytes(b"text")

    with pytest.raises(AudioProcessingError, match="empty"):
        audio_duration_seconds(empty)
    with pytest.raises(AudioProcessingError, match="Unsupported"):
        audio_duration_seconds(unsupported)


def test_corrupt_audio_is_rejected(monkeypatch, tmp_path):
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"not audio")

    def raise_mutagen(path):
        raise RuntimeError("bad header")

    monkeypatch.setattr(audio_module, "MutagenFile", raise_mutagen)

    with pytest.raises(AudioProcessingError, match="corrupt|unreadable"):
        audio_duration_seconds(corrupt)
