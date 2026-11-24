from __future__ import annotations

import io
import subprocess
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg

from bodhan_mcp.client import BodhanClient
from bodhan_mcp.models import Usage
from bodhan_mcp.tools.ocr import ocr_document
from bodhan_mcp.tools.speak import speak_text
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text
from bodhan_mcp.workspace import Workspace


def wav_bytes(seconds: float = 0.1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\0\0" * int(8_000 * seconds))
    return output.getvalue()


class MockBodhanClient(BodhanClient):
    """Mock client simulating API responses for testing."""

    def __init__(self):
        self.transcribed_files = []

    def transcribe(self, audio_bytes, filename, language=None):
        self.transcribed_files.append((filename, len(audio_bytes)))
        return {
            "text": "नमस्ते दुनिया",
            "language": language or "hi",
            "duration": 5.0,
            "usage": Usage(model="indic-transcribe", cost_inr=0.05),
        }

    def translate(self, text, target_language, source_language=None):
        return {
            "translated_text": f"Translated to {target_language}: {text}",
            "usage": Usage(model="indic-translate", cost_inr=0.02),
        }

    def speak(self, text, language, voice_id=None):
        return {
            "audio_bytes": wav_bytes(),
            "format": "wav",
            "usage": Usage(model="indic-speak", cost_inr=0.03),
        }

    def ocr(self, image_bytes, filename, language=None):
        return {
            "text": "Extracted OCR Document Text",
            "usage": Usage(model="indic-ocr", cost_inr=0.01),
        }


def test_transcribe_tool():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = Workspace(root)

        audio_path = root / "test_audio.wav"
        audio_path.write_bytes(wav_bytes(seconds=1))

        client = MockBodhanClient()
        res = transcribe_audio(ws, "test_audio.wav", language="hi", client=client)

        assert res["ok"] is True
        assert res["data"]["transcript"] == "नमस्ते दुनिया"
        assert len(res["artifacts"]) == 1
        assert res["data"]["coverage_complete"] is True
        assert len(client.transcribed_files) == 1


def test_transcribe_splits_long_wav_into_safe_windows():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = Workspace(root)
        audio_path = root / "long_audio.wav"
        audio_path.write_bytes(wav_bytes(seconds=31))
        client = MockBodhanClient()

        res = transcribe_audio(ws, "long_audio.wav", client=client)

        assert res["ok"] is True
        assert res["data"]["chunk_count"] == 2
        assert len(client.transcribed_files) == 2


def test_transcribe_splits_long_compressed_audio(tmp_path):
    source = tmp_path / "long.wav"
    source.write_bytes(wav_bytes(seconds=31))
    compressed = tmp_path / "long.mp3"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            str(compressed),
        ],
        check=True,
        capture_output=True,
    )
    client = MockBodhanClient()

    result = transcribe_audio(Workspace(tmp_path), "long.mp3", client=client)

    assert result["ok"] is True
    assert result["data"]["chunk_count"] == 2
    assert len(client.transcribed_files) == 2


def test_translate_tool():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = Workspace(root)

        client = MockBodhanClient()
        res = translate_text(
            ws, "Hello world", target_language="ta", source_language="en", client=client
        )

        assert res["ok"] is True
        assert "Translated to ta" in res["data"]["translation"]
        assert len(res["artifacts"]) == 1


def test_translation_processes_all_context_chunks():
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Workspace(tmpdir)
        client = MockBodhanClient()
        source_text = ("A complete sentence. " * 1_000).strip()

        result = translate_text(
            workspace,
            source_text,
            target_language="ta",
            client=client,
        )

        assert result["ok"] is True
        assert result["data"]["chunk_count"] > 1
        assert result["data"]["translated_chunk_count"] == result["data"]["chunk_count"]
        assert result["data"]["coverage_complete"] is True


def test_speak_tool():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = Workspace(root)

        client = MockBodhanClient()
        res = speak_text(ws, "Hello audio", language="hi", client=client)

        assert res["ok"] is True
        assert res["data"]["audio_format"] == "wav"
        assert len(res["artifacts"]) == 1


def test_speak_chunks_long_input_automatically():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = Workspace(root)

        long_text = "a" * 501
        client = MockBodhanClient()
        res = speak_text(ws, long_text, language="hi", client=client)

        assert res["ok"] is True
        assert res["data"]["chunk_count"] == 2
        assert res["data"]["verification"]["complete"] is True


def test_ocr_image_tool():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = Workspace(root)

        img_path = root / "sample.png"
        img_path.write_bytes(b"fake png image bytes")

        client = MockBodhanClient()
        res = ocr_document(ws, "sample.png", language="en", client=client)

        assert res["ok"] is True
        assert res["data"]["text"] == "Extracted OCR Document Text"
        assert len(res["artifacts"]) == 1


def test_ocr_processes_all_pdf_pages(monkeypatch, tmp_path):
    import bodhan_mcp.tools.ocr as ocr_module

    pdf_path = tmp_path / "multi-page.pdf"
    pdf_path.write_bytes(b"mock pdf")
    monkeypatch.setattr(
        ocr_module,
        "_render_pdf_pages",
        lambda path, page_number: ([(1, b"page one"), (2, b"page two")], 2),
    )
    result = ocr_document(
        Workspace(tmp_path),
        "multi-page.pdf",
        client=MockBodhanClient(),
    )

    assert result["ok"] is True
    assert result["data"]["pages_processed"] == 2
    assert result["data"]["total_pages"] == 2
    assert result["data"]["coverage_complete"] is True
    assert "Page 1" in result["data"]["text"]
    assert "Page 2" in result["data"]["text"]

def test_cache_deduplicates_identical_model_calls_in_same_run(tmp_path):
    from bodhan_mcp.cache import DiskCache

    cache = DiskCache(tmp_path / "cache")
    ws = Workspace(tmp_path)
    calls = 0

    class CountingClient(MockBodhanClient):
        def translate(self, text, target_language, source_language=None):
            nonlocal calls
            calls += 1
            return super().translate(text, target_language, source_language)

    client = CountingClient()
    first = translate_text(ws, "Hello 1/2 world", "ta", client=client, cache=cache)
    second = translate_text(ws, "Hello 1/2 world", "ta", client=client, cache=cache)

    assert first["ok"] is True
    assert second["ok"] is True
    assert calls == 1
    assert first["usage"]["cached"] is False
    assert second["usage"]["cached"] is True
    assert second["usage"]["cost_inr"] == 0.0
    assert second["usage"]["avoided_inr"] > 0.0


def test_translate_and_speak_accept_literal_text_with_slashes(tmp_path):
    ws = Workspace(tmp_path)
    client = MockBodhanClient()

    tr = translate_text(ws, "Meet on 12/05/2026 at 1/2 price", "hi", client=client)
    sp = speak_text(ws, "Ratio is 3/4", "bn", voice_gender="female", client=client)

    assert tr["ok"] is True
    assert sp["ok"] is True
    assert sp["data"]["voice_id"] == "Ishita"


def test_transcribe_generates_valid_srt_and_translate_preserves_timestamps(tmp_path):
    ws = Workspace(tmp_path)
    audio_path = ws.root / "long_audio.wav"
    audio_path.write_bytes(wav_bytes(seconds=31))
    client = MockBodhanClient()

    srt_res = transcribe_audio(
        ws, "long_audio.wav", language="hi", client=client, output_format="srt"
    )
    assert srt_res["ok"] is True
    assert srt_res["data"]["output_format"] == "srt"
    assert "00:00:00,000 --> 00:00:29,500" in srt_res["data"]["transcript"]
    art_ref = f"artifact:{srt_res['artifacts'][0]['ref']}"

    tr_srt = translate_text(ws, art_ref, target_language="en", client=client)
    assert tr_srt["ok"] is True
    assert "00:00:00,000 --> 00:00:29,500" in tr_srt["data"]["translation"]
    assert tr_srt["artifacts"][0]["path"].endswith(".srt")


def test_ocr_converts_tiff_to_png_before_calling_bodhan(tmp_path):
    from PIL import Image

    ws = Workspace(tmp_path)
    tiff_path = tmp_path / "scan.tiff"
    Image.new("RGB", (20, 20), color="white").save(tiff_path, format="TIFF")

    seen: list[str] = []

    class TiffCheckClient(MockBodhanClient):
        def ocr(self, image_bytes, filename, language=None):
            seen.append(filename)
            assert image_bytes.startswith(b"\x89PNG")
            return super().ocr(image_bytes, filename, language)

    res = ocr_document(ws, "scan.tiff", client=TiffCheckClient())
    assert res["ok"] is True
    assert seen == ["scan.png"]


def test_speak_merges_multiple_mp3_chunks_into_single_audio_file(tmp_path):
    ws = Workspace(tmp_path)
    wav_source = tmp_path / "src.wav"
    wav_source.write_bytes(wav_bytes(seconds=0.5))
    mp3_bytes = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(wav_source),
            "-f",
            "mp3",
            "-codec:a",
            "libmp3lame",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout

    class Mp3Client(MockBodhanClient):
        def speak(self, text, language, voice_id=None):
            return {
                "audio_bytes": mp3_bytes,
                "format": "mp3",
                "usage": Usage(model="indic-speak", cost_inr=0.03),
            }

    res = speak_text(ws, "a" * 501, language="ta", client=Mp3Client())

    assert res["ok"] is True
    assert res["data"]["chunk_count"] == 2
    assert res["data"]["audio_format"] == "mp3"
    assert len(res["artifacts"]) == 1
    assert res["artifacts"][0]["path"].endswith(".mp3")
    assert len(res["data"]["audio_paths"]) == 1