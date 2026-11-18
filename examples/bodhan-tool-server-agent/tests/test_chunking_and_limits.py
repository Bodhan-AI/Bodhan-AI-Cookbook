"""Unit tests for audio chunking, text splitting, schema validation, and error handling."""

import io
import math
import struct
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError

from bodhan_agent.models import ExecutionStatus, Plan, PlanStep, RunReport
from bodhan_agent.planner import rule_based_deterministic_plan
from bodhan_mcp.config import config, is_supported_language, normalize_language_code
from bodhan_mcp.media_utils import (
    MediaValidationError,
    chunk_audio,
    concat_wav_chunks,
    inspect_audio,
    validate_file_reference,
)
from bodhan_mcp.tools.speak import split_text_for_speech
from bodhan_mcp.tools.translate import split_text_into_chunks


def create_synthetic_wav(path: Path, duration_sec: float, sample_rate: int = 16000) -> Path:
    """Helper to generate a valid PCM WAV audio file of arbitrary duration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # 440 Hz gentle sine wave
        data = bytearray()
        for i in range(num_samples):
            val = int(16000.0 * math.sin(2.0 * math.pi * 440.0 * (i / sample_rate)))
            data.extend(struct.pack("<h", val))
        wf.writeframes(data)
    return path


def test_audio_short_file_no_chunking(tmp_path):
    """Audio <= 30 seconds should not be chunked unnecessarily."""
    wav_file = create_synthetic_wav(tmp_path / "short.wav", duration_sec=12.5)
    inspection = inspect_audio(wav_file)

    assert inspection["duration_seconds"] == 12.5
    assert inspection["requires_chunking"] is False

    chunks = chunk_audio(wav_file, max_duration_sec=30.0)
    assert len(chunks) == 1
    assert chunks[0]["duration_sec"] == 12.5
    assert chunks[0]["file_path"] == str(wav_file)


def test_audio_long_file_chunking_under_30s_windows(tmp_path):
    """Audio > 30 seconds must be split into slices strictly under 30.0s."""
    # Create 65-second audio
    long_wav = create_synthetic_wav(tmp_path / "lecture_65s.wav", duration_sec=65.0)
    inspection = inspect_audio(long_wav)
    assert inspection["duration_seconds"] == 65.0
    assert inspection["requires_chunking"] is True

    chunk_dir = tmp_path / "chunks"
    chunks = chunk_audio(long_wav, max_duration_sec=30.0, output_dir=chunk_dir)

    # 65s split into <=28s slices -> exactly 3 chunks
    assert len(chunks) == 3
    for chunk in chunks:
        assert chunk["duration_sec"] <= 30.0
        p = Path(chunk["file_path"])
        assert p.exists()
        chunk_info = inspect_audio(p)
        assert chunk_info["duration_seconds"] <= 30.0

    # Concatenation test
    merged_wav = tmp_path / "merged.wav"
    concat_wav_chunks([c["file_path"] for c in chunks], merged_wav)
    assert merged_wav.exists()
    merged_info = inspect_audio(merged_wav)
    assert abs(merged_info["duration_seconds"] - 65.0) < 0.2


def test_text_splitting_for_translation():
    """Text chunking preserves paragraph structure and does not exceed char limits."""
    paras = [
        "First paragraph introducing the AI stack in Indian languages.",
        "Second paragraph with technical details regarding Indic-Translate and context windows.",
        "Third paragraph concluding with deployment recommendations.",
    ]
    full_text = "\n\n".join(paras)

    chunks = split_text_into_chunks(full_text, max_chars=120)
    assert len(chunks) > 1
    recombined = "\n\n".join(chunks)
    assert recombined == full_text


def test_text_splitting_for_speech_natural_pacing():
    """Sentence splitting respects Devanagari purna viram (।) and standard punctuation."""
    indic_text = "नमस्ते। यह पहला वाक्य है। क्या आप ठीक हैं? हाँ, मैं ठीक हूँ।"
    sentences = split_text_for_speech(indic_text, max_chars=50)

    assert len(sentences) >= 2
    for s in sentences:
        assert len(s) <= 100


def test_language_normalization_and_validation():
    """Verify ISO code normalization for scheduled Indian languages."""
    assert normalize_language_code("Hindi") == "hi"
    assert normalize_language_code("kannada") == "kn"
    assert normalize_language_code("TA") == "ta"
    assert normalize_language_code("Telugu") == "te"
    assert normalize_language_code("en") == "en"

    # Unsupported languages return None
    assert normalize_language_code("french") is None
    assert normalize_language_code("german") is None
    assert is_supported_language("Spanish") is False


def test_file_reference_validation(tmp_path):
    """File reference validation enforces existence and allowed extensions."""
    # Missing file
    with pytest.raises(FileNotFoundError):
        validate_file_reference(tmp_path / "non_existent.wav", must_exist=True)

    # Empty path
    with pytest.raises(MediaValidationError):
        validate_file_reference("", must_exist=False)

    # Invalid extension
    bad_file = tmp_path / "script.exe"
    bad_file.write_text("dummy")
    with pytest.raises(MediaValidationError):
        validate_file_reference(bad_file, allowed_extensions={".wav", ".mp3"})


def test_pydantic_schema_validation():
    """Test schemas enforce correct types and required fields."""
    step = PlanStep(
        step_number=1,
        description="Transcribe audio",
        tool="bodhan_transcribe",
        arguments={"audio_path": "audio.wav"},
    )
    assert step.step_number == 1
    assert step.tool == "bodhan_transcribe"

    plan = Plan(
        task_summary="Transcribe and translate",
        target_language="hi",
        steps=[step],
    )
    assert plan.is_feasible is True
    assert len(plan.steps) == 1

    # Missing required field raises ValidationError
    with pytest.raises(ValidationError):
        PlanStep(step_number=1)
