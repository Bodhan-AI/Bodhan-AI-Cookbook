"""Tests for media inspection, chunking, and transcribe_audio tool (Slice 5)."""

from pathlib import Path

import httpx
import pytest

from bodhan_mcp import errors
from bodhan_mcp.clients.base import BaseBodhanClient
from bodhan_mcp.clients.bodhan import BodhanClient
from bodhan_mcp.schemas import InspectOut, TranscribeOut
from bodhan_mcp.tools.media import chunk_audio, get_voice_library, inspect_media
from bodhan_mcp.tools.transcribe import transcribe_audio


def test_inspect_media(short_wav: Path, small_pdf: Path, sample_image: Path, tmp_path: Path):
    """Verify inspect_media on audio, pdf, image, and missing files."""
    # 1. Missing file -> FILE_NOT_FOUND
    res_missing = inspect_media(str(tmp_path / "not_there.wav"))
    assert isinstance(res_missing, errors.ToolErrorPayload)
    assert res_missing.code == errors.FILE_NOT_FOUND

    # 2. Audio file
    res_audio = inspect_media(str(short_wav))
    assert isinstance(res_audio, InspectOut)
    assert res_audio.kind == "audio"
    assert res_audio.duration_s == pytest.approx(1.0, 0.05)
    assert "wav" in res_audio.format

    # 3. PDF file
    res_pdf = inspect_media(str(small_pdf))
    assert isinstance(res_pdf, InspectOut)
    assert res_pdf.kind == "pdf"
    assert res_pdf.pages == 1
    assert res_pdf.format == "pdf"

    # 4. Image file
    res_img = inspect_media(str(sample_image))
    assert isinstance(res_img, InspectOut)
    assert res_img.kind == "image"
    assert res_img.pages == 1


def test_chunk_audio_tool(short_wav: Path, tmp_path: Path):
    """Verify chunk_audio tool splits files and reports errors."""
    # 1. Missing file
    err = chunk_audio(str(tmp_path / "missing.wav"))
    assert isinstance(err, errors.ToolErrorPayload)
    assert err.code == errors.FILE_NOT_FOUND

    # 2. Short audio -> single chunk
    out = chunk_audio(str(short_wav), out_dir=str(tmp_path / "single_chunk"))
    assert hasattr(out, "chunks")
    assert len(out.chunks) == 1


def test_get_voice_library_tool():
    """Verify get_voice_library returns valid voices."""
    out = get_voice_library()
    assert "hi" in out.voices
    assert "ta" in out.voices
    assert "en" not in out.voices  # Standing Rule F6: speak excludes en


@pytest.mark.asyncio
async def test_transcribe_audio_validations(short_wav: Path, tmp_path: Path):
    """Verify transcribe_audio validates paths and language hints."""
    # Missing file
    err_missing = await transcribe_audio(str(tmp_path / "ghost.wav"))
    assert isinstance(err_missing, errors.ToolErrorPayload)
    assert err_missing.code == errors.FILE_NOT_FOUND

    # Unsupported language (e.g. French)
    err_lang = await transcribe_audio(str(short_wav), language_hint="fr")
    assert isinstance(err_lang, errors.ToolErrorPayload)
    assert err_lang.code == errors.UNSUPPORTED_LANGUAGE


@pytest.mark.asyncio
async def test_transcribe_audio_short_clip(short_wav: Path):
    """Verify transcribe_audio single-chunk path on a short clip."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "नमस्ते भारत",
                "language": "hi",
                "language_confidence": 0.95,
                "segments": [
                    {"start": 0.0, "end": 0.5, "text": "नमस्ते"},
                    {"start": 0.5, "end": 1.0, "text": "भारत"},
                ],
            },
        )

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    result = await transcribe_audio(str(short_wav), language_hint="hi", client=bodhan)
    assert isinstance(result, TranscribeOut)
    assert result.transcript == "नमस्ते भारत"
    assert result.language == "hi"
    assert len(result.segments) == 2
    assert result.cost_inr > 0.0
    await base_client.close()


@pytest.mark.asyncio
async def test_transcribe_audio_absorbs_chunking(tmp_path: Path):
    """Verify transcribe_audio automatically absorbs chunking for audio > 30s."""
    import math

    import numpy as np
    import soundfile as sf

    # Generate a 45s audio file with a pause at 22s
    sr = 16000
    duration_s = 45.0
    num_samples = int(sr * duration_s)
    t = np.linspace(0, duration_s, num_samples, endpoint=False)
    sig = 0.5 * np.sin(2 * math.pi * 440 * t)

    # Pause at 22.0 - 22.5s
    sig[int(22.0 * sr) : int(22.5 * sr)] = 0.0

    long_audio = tmp_path / "long_speech.wav"
    sf.write(str(long_audio), sig.astype("float32"), sr)

    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            json={
                "text": f"भाग {call_count}",
                "language": "hi",
                "language_confidence": 0.92,
                "segments": [
                    {"start": 0.0, "end": 5.0, "text": f"भाग {call_count}"},
                ],
            },
        )

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    res = await transcribe_audio(str(long_audio), language_hint="hi", client=bodhan)
    assert isinstance(res, TranscribeOut)
    # 45s audio sliced into 2 chunks
    assert call_count == 2
    assert "भाग 1" in res.transcript
    assert "भाग 2" in res.transcript
    assert len(res.segments) == 2

    # Second segment timestamp must be remapped past the cut point (~22s)
    assert res.segments[1].start_s >= 20.0
    await base_client.close()
