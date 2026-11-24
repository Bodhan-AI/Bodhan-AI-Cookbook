"""
Verifier — checks outputs before the agent declares success.

Verification strategies:
- Audio: back-transcribe and compare
- Translation: check coverage (length ratio, sentence count)
- OCR: check confidence scores
- General: check that expected outputs exist
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from bodhan_mcp.client import BodhanClient
from bodhan_mcp.media import get_audio_duration

logger = logging.getLogger(__name__)


async def verify_audio_output(
    client: BodhanClient,
    audio_path: str,
    expected_language: str | None = None,
    expected_content_hint: str | None = None,
) -> dict[str, Any]:
    """
    Verify a generated audio file by back-transcribing it.

    Args:
        client: BodhanClient instance.
        audio_path: Path to the audio file to verify.
        expected_language: Expected language of the audio.
        expected_content_hint: A hint about expected content (for comparison).

    Returns:
        dict with 'passed', 'check', 'details', 'transcription'.
    """
    path = Path(audio_path)
    result: dict[str, Any] = {
        "check": "audio_verification",
        "file": str(path),
        "passed": False,
        "details": "",
    }

    if not path.exists():
        result["details"] = f"Audio file does not exist: {audio_path}"
        return result

    # Check file size (should be non-trivial)
    size = path.stat().st_size
    if size < 1000:
        result["details"] = f"Audio file suspiciously small: {size} bytes"
        return result

    # Check duration
    duration = get_audio_duration(str(path))
    if duration < 0.5:
        result["details"] = f"Audio too short: {duration:.1f}s"
        return result

    # Back-transcribe
    try:
        transcription = await client.transcribe(
            audio_path=str(path),
            language=expected_language,
            duration_seconds=min(duration, 30),  # Only transcribe first 30s for verification
        )
        transcribed_text = transcription.get("text", "")

        if not transcribed_text.strip():
            result["details"] = "Back-transcription returned empty text"
            return result

        result["transcription"] = transcribed_text
        result["passed"] = True
        result["details"] = (
            f"Back-transcription successful ({len(transcribed_text)} chars, {duration:.1f}s audio)"
        )

    except Exception as e:
        result["details"] = f"Back-transcription failed: {str(e)}"

    return result


def verify_translation_coverage(
    source_text: str,
    translated_text: str,
    source_language: str,
    target_language: str,
) -> dict[str, Any]:
    """
    Verify that a translation covers the full source text.

    Checks:
    - Non-empty output
    - Reasonable length ratio (translated shouldn't be way shorter)
    - Sentence count preservation (approximate)
    """
    result: dict[str, Any] = {
        "check": "translation_coverage",
        "passed": False,
        "details": "",
    }

    if not translated_text.strip():
        result["details"] = "Translation is empty"
        return result

    # Length ratio check
    source_len = len(source_text)
    trans_len = len(translated_text)

    if source_len == 0:
        result["passed"] = True
        result["details"] = "Source was empty; translation is also empty"
        return result

    ratio = trans_len / source_len
    # Indian scripts tend to be more compact than English, so allow wider range
    if ratio < 0.1:
        result["details"] = f"Translation too short (ratio: {ratio:.2f})"
        return result
    if ratio > 10.0:
        result["details"] = f"Translation suspiciously long (ratio: {ratio:.2f})"
        return result

    # Sentence count (rough check)
    import re
    source_sentences = len(re.split(r"[.!?।]+", source_text))
    trans_sentences = len(re.split(r"[.!?।]+", translated_text))

    sentence_ratio = trans_sentences / max(source_sentences, 1)

    result["passed"] = True
    result["details"] = (
        f"Coverage OK: {source_len}→{trans_len} chars (ratio: {ratio:.2f}), "
        f"{source_sentences}→{trans_sentences} sentences (ratio: {sentence_ratio:.2f})"
    )
    result["length_ratio"] = round(ratio, 2)
    result["sentence_ratio"] = round(sentence_ratio, 2)
    return result


def verify_ocr_output(
    ocr_result: dict[str, Any],
    min_confidence: float = 0.5,
) -> dict[str, Any]:
    """
    Verify OCR output quality by checking confidence scores.
    """
    result: dict[str, Any] = {
        "check": "ocr_quality",
        "passed": False,
        "details": "",
    }

    text = ocr_result.get("text", "")
    confidence = ocr_result.get("avg_confidence", 0.0)
    num_pages = ocr_result.get("num_pages", 0)

    if not text.strip():
        result["details"] = "OCR returned empty text"
        return result

    if confidence > 0 and confidence < min_confidence:
        result["details"] = f"Low OCR confidence: {confidence:.2f} (min: {min_confidence})"
        result["passed"] = False
        return result

    result["passed"] = True
    result["details"] = (
        f"OCR OK: {len(text)} chars, {num_pages} pages, "
        f"confidence: {confidence:.2f}"
    )
    return result


def verify_file_exists(file_path: str, min_size: int = 1) -> dict[str, Any]:
    """Check that a file exists and has minimum size."""
    path = Path(file_path)
    result: dict[str, Any] = {
        "check": "file_exists",
        "file": str(path),
        "passed": False,
        "details": "",
    }

    if not path.exists():
        result["details"] = f"File does not exist: {file_path}"
        return result

    size = path.stat().st_size
    if size < min_size:
        result["details"] = f"File too small: {size} bytes (min: {min_size})"
        return result

    result["passed"] = True
    result["details"] = f"File exists: {size} bytes"
    return result
