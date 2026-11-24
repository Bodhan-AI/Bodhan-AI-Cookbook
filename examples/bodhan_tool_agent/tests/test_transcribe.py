"""Tests for Indic-Transcribe MCP tool using synthetic audio and httpx.MockTransport (Zero Live API Calls)."""

import struct
import wave
from pathlib import Path
from typing import List, Tuple
import httpx
import pytest

from bodhan_mcp.tools.transcribe import (
    IndicTranscribeTool,
)


def create_synthetic_wave_file(
    file_path: Path,
    total_seconds: float,
    silence_intervals: List[Tuple[float, float]] = None,
    sample_rate: int = 16000,
) -> Path:
    """Synthesizes a valid mono 16-bit PCM WAV file with optional silence intervals."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    silences = silence_intervals or []

    num_samples = int(total_seconds * sample_rate)
    samples = bytearray()

    for i in range(num_samples):
        t = i / sample_rate
        # Check if t falls within any silence interval
        in_silence = any(start <= t <= end for start, end in silences)
        val = 0 if in_silence else int(3000 * ((i % 40) / 20.0 - 1.0))
        samples.extend(struct.pack("<h", val))

    with wave.open(str(file_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples)

    return file_path


@pytest.fixture
def tmp_transcribe_cache(tmp_path: Path):
    """Provides an isolated cache directory for Transcribe tests."""
    cache_dir = tmp_path / "test_transcribe_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@pytest.mark.asyncio
async def test_transcribe_70s_silence_splits_into_3_chunks(
    tmp_path: Path, tmp_transcribe_cache: Path
):
    """A 70-second audio file with silence intervals splits into exactly 3 chunks."""
    audio_path = tmp_path / "audio_70s.wav"
    # Silence at 24.0s-25.0s and 48.0s-49.0s
    create_synthetic_wave_file(
        audio_path,
        total_seconds=70.0,
        silence_intervals=[(24.0, 25.0), (48.0, 49.0)],
    )

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"text": f"Transcript chunk {call_count}"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranscribeTool(
        api_key="test_key", client=mock_client, cache_dir=tmp_transcribe_cache
    )

    result = await tool.transcribe(audio_path, language="hi")

    assert result.success is True
    assert result.chunks_count == 3
    assert len(result.chunks) == 3
    assert call_count == 3
    assert result.billed_chunks_count == 3

    # Check that chunks split near silences
    assert 20.0 <= result.chunks[0].end_second <= 29.0
    assert 40.0 <= result.chunks[1].end_second <= 58.0
    assert result.chunks[2].end_second == pytest.approx(70.0, 0.1)

    assert "Transcript chunk 1" in result.text
    assert "Transcript chunk 3" in result.text
    assert result.estimated_cost_inr > 0.0


@pytest.mark.asyncio
async def test_transcribe_35s_tone_no_silence_hard_cuts_at_29s(
    tmp_path: Path, tmp_transcribe_cache: Path
):
    """A 35-second continuous tone with no silence hard-cuts at 29.0s into 2 chunks."""
    audio_path = tmp_path / "audio_35s_tone.wav"
    create_synthetic_wave_file(audio_path, total_seconds=35.0, silence_intervals=[])

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"text": f"Tone segment {call_count}"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranscribeTool(
        api_key="test_key", client=mock_client, cache_dir=tmp_transcribe_cache
    )

    result = await tool.transcribe(audio_path)

    assert result.success is True
    assert result.chunks_count == 2
    assert len(result.chunks) == 2
    assert call_count == 2

    # Chunk 1 must be hard-cut at 29.0s
    assert result.chunks[0].start_second == 0.0
    assert result.chunks[0].end_second == 29.0

    # Chunk 2 spans remainder to 35.0s
    assert result.chunks[1].start_second == 29.0
    assert result.chunks[1].end_second == pytest.approx(35.0, 0.1)


@pytest.mark.asyncio
async def test_transcribe_cache_hit_per_chunk(tmp_path: Path, tmp_transcribe_cache: Path):
    """Re-transcribing identical audio file hits cache, makes 0 HTTP calls, Rs 0.00 cost."""
    audio_path = tmp_path / "audio_short.wav"
    create_synthetic_wave_file(audio_path, total_seconds=10.0)

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"text": "Namaste Duniya"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranscribeTool(
        api_key="test_key", client=mock_client, cache_dir=tmp_transcribe_cache
    )

    # Call 1: Miss
    res1 = await tool.transcribe(audio_path, language="hi")
    assert res1.success is True
    assert res1.chunks[0].cache_hit is False
    assert res1.billed_chunks_count == 1
    assert call_count == 1
    assert res1.text == "Namaste Duniya"

    # Call 2: Hit
    res2 = await tool.transcribe(audio_path, language="hi")
    assert res2.success is True
    assert res2.chunks[0].cache_hit is True
    assert res2.cached_chunks_count == 1
    assert res2.billed_chunks_count == 0
    assert res2.estimated_cost_inr == 0.0
    assert call_count == 1  # 0 new HTTP requests!
    assert res2.text == "Namaste Duniya"


@pytest.mark.asyncio
async def test_transcribe_415_local_ffprobe_failure(tmp_path: Path, tmp_transcribe_cache: Path):
    """When a corrupted file fails local ffprobe parsing, returns StructuredError UNSUPPORTED_MEDIA_TYPE."""
    # Write garbage file with .wav extension
    bad_audio = tmp_path / "corrupt.wav"
    bad_audio.write_bytes(b"NOT_A_VALID_WAV_HEADER_DATA_12345")

    tool = IndicTranscribeTool(api_key="test_key", cache_dir=tmp_transcribe_cache)
    result = await tool.transcribe(bad_audio)

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "UNSUPPORTED_MEDIA_TYPE"


@pytest.mark.asyncio
async def test_transcribe_415_remote_api_rejection(tmp_path: Path, tmp_transcribe_cache: Path):
    """When a valid audio file passes local ffprobe but the remote API rejects it with HTTP 415."""
    audio_path = tmp_path / "valid_audio.wav"
    create_synthetic_wave_file(audio_path, total_seconds=5.0)

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            415, text="Unsupported Media Type: audio encoding rejected by Bodhan API"
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranscribeTool(
        api_key="test_key", client=mock_client, cache_dir=tmp_transcribe_cache
    )

    result = await tool.transcribe(audio_path)
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "UNSUPPORTED_MEDIA_TYPE"
    assert result.error.retryable is False
    assert call_count == 1  # Reached fake HTTP layer and handled HTTP 415 correctly


@pytest.mark.asyncio
async def test_transcribe_bad_path(tmp_transcribe_cache: Path):
    """Non-existent audio path returns StructuredError without any HTTP calls."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranscribeTool(
        api_key="test_key", client=mock_client, cache_dir=tmp_transcribe_cache
    )

    result = await tool.transcribe("non_existent_audio.wav")
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "FILE_NOT_FOUND"
    assert call_count == 0


@pytest.mark.asyncio
async def test_transcribe_missing_ffmpeg_handling(tmp_path: Path, tmp_transcribe_cache: Path):
    """If ffmpeg/ffprobe binary paths are invalid or missing, returns StructuredError FFMPEG_MISSING."""
    audio_path = tmp_path / "audio_test.wav"
    create_synthetic_wave_file(audio_path, total_seconds=5.0)

    # Point binaries to non-existent executables
    tool = IndicTranscribeTool(
        api_key="test_key",
        ffmpeg_bin="C:\\nonexistent\\path\\ffmpeg.exe",
        ffprobe_bin="C:\\nonexistent\\path\\ffprobe.exe",
        cache_dir=tmp_transcribe_cache,
    )

    result = await tool.transcribe(audio_path)
    assert result.success is False
    assert result.error is not None
    assert result.error.code in ("FFMPEG_MISSING", "AUDIO_PROBE_ERROR")


def test_transcribe_preflight_cost_estimate(tmp_path: Path):
    """estimate_cost reports duration, chunks, and cost without any HTTP calls."""
    audio_path = tmp_path / "audio_45s.wav"
    create_synthetic_wave_file(audio_path, total_seconds=45.0)

    tool = IndicTranscribeTool(api_key="test_key")
    est = tool.estimate_cost(audio_path)

    assert est["duration_seconds"] == pytest.approx(45.0, 0.1)
    assert est["estimated_chunks"] == 2
    # 45s = 0.75 min * 0.10 = Rs 0.075 pro-rata
    assert est["cost_pro_rata_inr"] == pytest.approx(0.075, 0.001)
    # 2 chunks * Rs 0.10 = Rs 0.20 worst case
    assert est["cost_worst_case_inr"] == pytest.approx(0.20, 0.001)
    assert est["estimated_cost_inr"] == pytest.approx(0.075, 0.001)
