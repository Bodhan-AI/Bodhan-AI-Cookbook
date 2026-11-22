import asyncio
import json
from pathlib import Path

from bodhan_mcp.client import TranslateResult
from bodhan_mcp.fake_client import FakeBodhanClient
from bodhan_mcp.server import create_server, inspect_media, list_voices, make_subtitles
from bodhan_mcp.textutil import TTS_CHUNK_CHARS, chunk_text
from PIL import Image


def _payload(result):
    return json.loads(result.content[0].text)


def test_server_registers_only_the_phase_two_tools():
    server = create_server(FakeBodhanClient())
    tools = getattr(server._tool_manager, "_tools", {})
    names = set(tools)
    assert names == {
        "bodhan_transcribe",
        "bodhan_translate",
        "bodhan_synthesize",
        "bodhan_ocr",
        "inspect_media",
        "list_voices",
        "check_language_support",
        "make_subtitles",
    }


def test_helpers_are_deterministic(tmp_path):
    missing = inspect_media(str(tmp_path / "missing.wav"))
    assert missing["ok"] is False
    assert missing["error"]["code"] == "FILE_NOT_FOUND"

    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert inspect_media(str(pdf))["kind"] == "pdf"

    voices = list_voices("ta", "female")
    assert voices["ok"] is True
    assert voices["voice"] == "Anitha"


def test_make_subtitles_returns_srt():
    result = make_subtitles([{"start": 0.0, "end": 1.0, "text": "Hello"}])
    assert result["ok"] is True
    assert "00:00:00,000 --> 00:00:01,000" in result["srt"]


def test_make_subtitles_tool_uses_timed_json_source(tmp_path):
    source = tmp_path / "segments.json"
    source.write_text(json.dumps([
        {"start": 0.0, "end": 1.25, "text": "first phrase"},
        {"start": 1.25, "end": 2.5, "text": "second phrase"},
    ]), encoding="utf-8")
    result = asyncio.run(create_server(FakeBodhanClient()).call_tool(
        "make_subtitles",
        {"source_path": str(source)},
    ))
    data = _payload(result)

    assert data["ok"] is True
    assert "00:00:00,000 --> 00:00:01,250" in data["srt"]
    assert "00:00:01,250 --> 00:00:02,500" in data["srt"]


def test_make_subtitles_tool_rejects_audio_as_timing_source(tmp_path):
    audio = tmp_path / "untimed.wav"
    audio.write_bytes(b"not relevant")
    result = asyncio.run(create_server(FakeBodhanClient()).call_tool(
        "make_subtitles",
        {"source_path": str(audio)},
    ))
    data = _payload(result)

    assert data["ok"] is False
    assert data["error"]["code"] == "UNSUPPORTED_TIMESTAMP_SOURCE"


def test_synthesis_chunks_are_combined_into_valid_wav(tmp_path):
    text = "Hello. " * 40
    output = tmp_path / "long-speech.wav"
    server = create_server(FakeBodhanClient())

    result = asyncio.run(server.call_tool(
        "bodhan_synthesize",
        {"text": text, "language": "en", "output_path": str(output)},
    ))
    data = _payload(result)

    assert data["ok"] is True
    assert data["chunks"] > 1
    assert Path(data["artifact_path"]).read_bytes().startswith(b"RIFF")
    assert data["artifact_path"].endswith(".wav")
    assert FakeBodhanClient().transcribe(data["artifact_path"]).text == " ".join(
        chunk_text(text, TTS_CHUNK_CHARS)
    )


def test_transcription_does_not_send_unsplit_audio_when_probe_fails(tmp_path, monkeypatch):
    source = tmp_path / "not-probeable.wav"
    source.write_bytes(b"not audio")

    class CountingFake(FakeBodhanClient):
        transcribe_calls = 0

        def transcribe(self, audio_path, language=None):
            self.transcribe_calls += 1
            return super().transcribe(audio_path, language)

    client = CountingFake()
    server = create_server(client)

    def fail_probe(path):
        raise RuntimeError("ffprobe is unavailable")

    monkeypatch.setattr("bodhan_mcp.server.probe_duration", fail_probe)
    result = asyncio.run(server.call_tool("bodhan_transcribe", {"audio_path": str(source)}))
    data = _payload(result)

    assert data["ok"] is False
    assert data["error"]["code"] == "TRANSCRIPTION_FAILED"
    assert client.transcribe_calls == 0


def test_translation_chunks_and_caps_output_tokens():
    class CountingFake(FakeBodhanClient):
        def __init__(self):
            self.requests = []

        def translate(self, text, target_language, source_language=None, script="native", max_output_tokens=None):
            self.requests.append((text, max_output_tokens))
            return TranslateResult(text="translated output")

    client = CountingFake()
    server = create_server(client)
    text = "a" * 2500

    result = asyncio.run(server.call_tool(
        "bodhan_translate",
        {"text": text, "target_language": "en"},
    ))
    data = _payload(result)

    assert data["ok"] is True
    assert data["chunks"] == 3
    assert len(client.requests) == 3
    assert all(len(chunk) <= 1200 for chunk, _ in client.requests)
    assert all(cap >= len(chunk) * 2 for chunk, cap in client.requests)


def test_ocr_renders_and_processes_every_pdf_page(tmp_path):
    pdf_path = tmp_path / "two-pages.pdf"
    first = Image.new("RGB", (40, 40), "white")
    second = Image.new("RGB", (40, 40), "white")
    first.save(pdf_path, "PDF", save_all=True, append_images=[second])

    class CountingFake(FakeBodhanClient):
        calls = 0

        def ocr(self, image_path):
            self.calls += 1
            assert Path(image_path).suffix == ".png"
            return super().ocr(image_path)

    client = CountingFake()
    result = asyncio.run(create_server(client).call_tool("bodhan_ocr", {"image_path": str(pdf_path)}))
    data = _payload(result)

    assert data["ok"] is True
    assert data["pages"] == 2
    assert data["cost_inr"] == 0.4
    assert client.calls == 2
