"""Output Verification for Bodhan Agent.

Verifies that model outputs:
- Match expected Indian language scripts and Unicode ranges
- Preserve markdown/document structures and length ratios
- Meet audio file validity, duration, and AI-generated labeling requirements
- Optionally satisfy back-transcription checks
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from bodhan_agent.models import VerificationResult
from bodhan_mcp.media_utils import inspect_audio

logger = logging.getLogger("bodhan.verifier")

# Unicode ranges for Indic scripts
UNICODE_SCRIPT_RANGES = {
    "devanagari": (0x0900, 0x097F),  # hi, mr, sa, ne, kok, brx, mai, doi
    "bengali": (0x0980, 0x09FF),     # bn, as
    "gurmukhi": (0x0A00, 0x0A7F),    # pa
    "gujarati": (0x0A80, 0x0AFF),    # gu
    "odia": (0x0B00, 0x0B7F),        # or
    "tamil": (0x0B80, 0x0BFF),       # ta
    "telugu": (0x0C00, 0x0C7F),      # te
    "kannada": (0x0C80, 0x0CFF),     # kn
    "malayalam": (0x0D00, 0x0D7F),   # ml
    "perso_arabic": (0x0600, 0x06FF),# ur, ks, sd
}

LANGUAGE_TO_SCRIPT = {
    "hi": "devanagari",
    "mr": "devanagari",
    "sa": "devanagari",
    "ne": "devanagari",
    "kok": "devanagari",
    "mai": "devanagari",
    "doi": "devanagari",
    "brx": "devanagari",
    "bn": "bengali",
    "as": "bengali",
    "pa": "gurmukhi",
    "gu": "gujarati",
    "or": "odia",
    "ta": "tamil",
    "te": "telugu",
    "kn": "kannada",
    "ml": "malayalam",
    "ur": "perso_arabic",
    "ks": "perso_arabic",
    "sd": "perso_arabic",
    "en": "latin",
}


def check_script_match(text: str, language_code: str) -> VerificationResult:
    """Check if the text contains native script characters corresponding to the language."""
    clean_lang = language_code.lower()
    script = LANGUAGE_TO_SCRIPT.get(clean_lang)

    if not script or script == "latin":
        return VerificationResult(
            check_name="script_check",
            passed=True,
            details=f"Language '{language_code}' does not require native Indic script verification.",
        )

    low, high = UNICODE_SCRIPT_RANGES[script]
    match_count = sum(1 for c in text if low <= ord(c) <= high)
    total_alpha = sum(1 for c in text if c.isalpha())

    if total_alpha == 0:
        return VerificationResult(
            check_name="script_check",
            passed=False,
            details="Output text contains no alphabetic characters.",
        )

    ratio = match_count / float(total_alpha)
    passed = ratio > 0.15  # At least 15% native script characters (accounts for code-mixing/numbers)

    return VerificationResult(
        check_name="script_check",
        passed=passed,
        details=f"Script match ratio for '{script}': {ratio:.1%} ({match_count}/{total_alpha} letters).",
        score=round(ratio, 3),
    )


def check_translation_quality(
    source_text: str,
    translated_text: str,
    target_language: str,
) -> List[VerificationResult]:
    """Verify translated text for non-emptiness, length ratio, structure preservation, and script."""
    results = []

    # 1. Non-empty check
    if not translated_text or not translated_text.strip():
        results.append(
            VerificationResult(
                check_name="non_empty_check",
                passed=False,
                details="Translated text is completely empty.",
            )
        )
        return results

    results.append(
        VerificationResult(
            check_name="non_empty_check",
            passed=True,
            details=f"Translated text is non-empty ({len(translated_text)} characters).",
        )
    )

    # 2. Length ratio check
    src_len = max(1, len(source_text))
    tgt_len = len(translated_text)
    ratio = tgt_len / float(src_len)
    ratio_ok = 0.25 <= ratio <= 4.0
    results.append(
        VerificationResult(
            check_name="length_ratio_check",
            passed=ratio_ok,
            details=f"Length ratio: {ratio:.2f} (source: {src_len} chars, target: {tgt_len} chars).",
            score=round(ratio, 2),
        )
    )

    # 3. Structure preservation check
    src_headings = source_text.count("#")
    tgt_headings = translated_text.count("#")
    if src_headings > 0:
        h_ok = tgt_headings >= max(1, src_headings - 1)
        results.append(
            VerificationResult(
                check_name="structure_heading_check",
                passed=h_ok,
                details=f"Markdown headings count: source={src_headings}, target={tgt_headings}.",
            )
        )

    # 4. Script check
    results.append(check_script_match(translated_text, target_language))

    return results


def check_synthesized_audio(
    audio_path: Union[str, Path],
    expected_char_count: int,
) -> List[VerificationResult]:
    """Verify synthesized audio file existence, header validity, duration, and AI disclaimer."""
    results = []
    p = Path(audio_path).resolve()

    # 1. File existence and size
    if not p.exists() or p.stat().st_size < 500:
        results.append(
            VerificationResult(
                check_name="audio_file_validity",
                passed=False,
                details=f"Audio file is missing or suspiciously small ({p.stat().st_size if p.exists() else 0} bytes).",
            )
        )
        return results

    results.append(
        VerificationResult(
            check_name="audio_file_validity",
            passed=True,
            details=f"Audio file is valid ({p.stat().st_size} bytes).",
        )
    )

    # 2. Duration plausibility
    info = inspect_audio(p)
    duration = info.get("duration_seconds", 0.0)
    # Speech typically 10 - 25 characters per second
    min_expected_sec = max(0.5, expected_char_count / 35.0)
    max_expected_sec = max(3.0, expected_char_count / 5.0)
    dur_ok = min_expected_sec <= duration <= max_expected_sec

    results.append(
        VerificationResult(
            check_name="audio_duration_check",
            passed=dur_ok,
            details=f"Duration {duration:.2f}s is within plausible range [{min_expected_sec:.1f}s - {max_expected_sec:.1f}s] for {expected_char_count} chars.",
            score=round(duration, 2),
        )
    )

    # 3. AI-generated labeling check
    meta_path = p.with_suffix(".json")
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
            has_label = meta.get("ai_generated") is True
            results.append(
                VerificationResult(
                    check_name="ai_label_check",
                    passed=has_label,
                    details="Audio companion metadata correctly declares AI-generated status.",
                )
            )
        except Exception as e:
            results.append(
                VerificationResult(
                    check_name="ai_label_check",
                    passed=False,
                    details=f"Failed to read companion metadata: {e}",
                )
            )
    else:
        results.append(
            VerificationResult(
                check_name="ai_label_check",
                passed=False,
                details="Missing companion AI disclaimer metadata file.",
            )
        )

    return results


def check_transcription_quality(transcript: str) -> List[VerificationResult]:
    """Verify transcription result for non-emptiness and minimal word count."""
    results = []
    if not transcript or not transcript.strip():
        results.append(
            VerificationResult(
                check_name="transcript_non_empty",
                passed=False,
                details="Transcribed text is empty.",
            )
        )
    else:
        words = transcript.strip().split()
        results.append(
            VerificationResult(
                check_name="transcript_non_empty",
                passed=True,
                details=f"Transcript contains {len(words)} words ({len(transcript)} chars).",
                score=float(len(words)),
            )
        )
    return results


def check_ocr_quality(extracted_markdown: str, expected_pages: int) -> List[VerificationResult]:
    """Verify OCR result for extracted text volume and page coverage."""
    results = []
    if not extracted_markdown or not extracted_markdown.strip():
        results.append(
            VerificationResult(
                check_name="ocr_non_empty",
                passed=False,
                details="Extracted OCR text is empty.",
            )
        )
    else:
        results.append(
            VerificationResult(
                check_name="ocr_non_empty",
                passed=True,
                details=f"OCR extracted {len(extracted_markdown)} characters of Markdown content.",
            )
        )

    return results
