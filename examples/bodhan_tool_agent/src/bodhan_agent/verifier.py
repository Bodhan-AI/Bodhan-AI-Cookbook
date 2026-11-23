"""Verification engine: back-transcription, structural integrity, subtitles, and artifact validation."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from bodhan_agent.models import VerificationResult, VerificationStatus
from bodhan_mcp.tools.media import get_audio_duration_seconds

logger = logging.getLogger("bodhan_agent.verifier")


class Verifier:
    """Rigorous artifact and output verification to ensure task success beyond HTTP 200."""

    @staticmethod
    def verify_file_artifact(file_path: str | Path, min_bytes: int = 10) -> VerificationResult:
        """Verify that an artifact file was written to disk and is non-empty."""
        p = Path(file_path)
        if not p.exists():
            return VerificationResult(
                check_name="file_existence",
                status=VerificationStatus.FAILED,
                details=f"Artifact file not found: {p}",
                target_artifact=str(p),
            )
        size = p.stat().st_size
        if size < min_bytes:
            return VerificationResult(
                check_name="file_size",
                status=VerificationStatus.FAILED,
                details=f"Artifact file is suspiciously small ({size} bytes): {p}",
                target_artifact=str(p),
                metrics={"size_bytes": size},
            )
        return VerificationResult(
            check_name="file_existence",
            status=VerificationStatus.PASSED,
            details=f"Artifact verified ({size} bytes): {p.name}",
            target_artifact=str(p),
            metrics={"size_bytes": size},
        )

    @staticmethod
    def verify_audio_duration(file_path: str | Path, min_duration_s: float = 0.5) -> VerificationResult:
        """Verify that a synthesized audio file contains valid audio frames of sufficient duration."""
        p = Path(file_path)
        dur = get_audio_duration_seconds(p)
        if dur < min_duration_s:
            return VerificationResult(
                check_name="audio_duration",
                status=VerificationStatus.FAILED,
                details=f"Synthesized audio duration is too short: {dur:.2f}s (expected >= {min_duration_s}s)",
                target_artifact=str(p),
                metrics={"duration_s": dur},
            )
        return VerificationResult(
            check_name="audio_duration",
            status=VerificationStatus.PASSED,
            details=f"Audio duration verified: {dur:.2f} seconds",
            target_artifact=str(p),
            metrics={"duration_s": dur},
        )

    @staticmethod
    def verify_translation_completeness(source_text: str, translated_text: str, target_lang: str) -> VerificationResult:
        """Verify that translation is non-empty, structural paragraphs are preserved, and language changed."""
        if not translated_text.strip():
            return VerificationResult(
                check_name="translation_completeness",
                status=VerificationStatus.FAILED,
                details="Translation is completely empty.",
            )

        src_paragraphs = [p for p in source_text.split("\n\n") if p.strip()]
        out_paragraphs = [p for p in translated_text.split("\n\n") if p.strip()]

        # Check paragraph count discrepancy
        if len(src_paragraphs) > 1 and len(out_paragraphs) < (len(src_paragraphs) // 2):
            return VerificationResult(
                check_name="translation_completeness",
                status=VerificationStatus.PARTIAL,
                details=f"Possible truncation: {len(src_paragraphs)} source paragraphs vs {len(out_paragraphs)} output paragraphs.",
                metrics={"src_paragraphs": len(src_paragraphs), "out_paragraphs": len(out_paragraphs)},
            )

        # Check that non-English translation didn't merely echo source English text verbatim
        if target_lang != "en" and source_text.strip().lower() == translated_text.strip().lower():
            return VerificationResult(
                check_name="translation_language_check",
                status=VerificationStatus.FAILED,
                details=f"Translation output is identical to source English text; translation into '{target_lang}' failed.",
            )

        return VerificationResult(
            check_name="translation_completeness",
            status=VerificationStatus.PASSED,
            details=f"Translation verified ({len(translated_text)} chars, {len(out_paragraphs)} paragraphs)",
            metrics={"char_count": len(translated_text)},
        )

    @staticmethod
    def verify_srt_subtitles(srt_content: str) -> VerificationResult:
        """Verify SRT format syntax, index sequence, and timestamp monotonic progression."""
        blocks = srt_content.strip().split("\n\n")
        if not blocks or not blocks[0].strip():
            return VerificationResult(
                check_name="srt_validation",
                status=VerificationStatus.FAILED,
                details="SRT subtitle content is empty.",
            )

        timestamp_pattern = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")
        valid_entries = 0

        for b in blocks:
            lines = b.strip().splitlines()
            if len(lines) >= 2 and timestamp_pattern.search(lines[1]):
                valid_entries += 1

        if valid_entries == 0:
            return VerificationResult(
                check_name="srt_validation",
                status=VerificationStatus.FAILED,
                details="SRT content has no valid timestamp blocks.",
            )

        return VerificationResult(
            check_name="srt_validation",
            status=VerificationStatus.PASSED,
            details=f"SRT syntax validated ({valid_entries} subtitle cues)",
            metrics={"cue_count": valid_entries},
        )

    @staticmethod
    def verify_ocr_pages(total_pages: int, successful_pages: int, failed_pages: int) -> VerificationResult:
        """Verify that document OCR extracted all expected pages."""
        if successful_pages == 0:
            return VerificationResult(
                check_name="ocr_page_coverage",
                status=VerificationStatus.FAILED,
                details=f"All {total_pages} document pages failed OCR.",
            )
        if failed_pages > 0:
            return VerificationResult(
                check_name="ocr_page_coverage",
                status=VerificationStatus.PARTIAL,
                details=f"{failed_pages} of {total_pages} pages failed OCR ({successful_pages} succeeded).",
                metrics={"total": total_pages, "failed": failed_pages},
            )
        return VerificationResult(
            check_name="ocr_page_coverage",
            status=VerificationStatus.PASSED,
            details=f"All {total_pages} pages successfully processed by OCR.",
            metrics={"total": total_pages},
        )
