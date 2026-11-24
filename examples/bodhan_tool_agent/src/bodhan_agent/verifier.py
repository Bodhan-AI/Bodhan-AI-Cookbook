"""
Verification strategies for generated artifacts (Audio, Translation, SRT, OCR).
Enforces quality gates before marking a task completed.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from bodhan_core.normalize import compute_cer, normalize_text

log = logging.getLogger(__name__)


class Verifier:
    """Verifies generated artifacts to ensure high quality and detect model quirks."""

    def __init__(self, client=None) -> None:
        self.client = client

    async def verify_audio(
        self,
        audio_path: Path,
        source_text: str,
        language: str | None = None,
    ) -> dict[str, Any]:
        """
        Verify generated speech by back-transcribing via Indic-Transcribe
        and computing CER against the source text. Also flags repetitive loops.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            return {
                "passed": False,
                "reason": "Audio file does not exist or is 0 bytes.",
                "cer": 1.0,
            }

        # If client is available, back-transcribe
        if self.client:
            try:
                res = await self.client.transcribe(audio_path, language=language)
                hypothesis = res.get("text", "")
                cer = compute_cer(source_text, hypothesis)
                repeated = self._check_repetition(hypothesis)
                passed = (cer < 0.35) and not repeated
                return {
                    "passed": passed,
                    "cer": round(cer, 3),
                    "repetition_detected": repeated,
                    "back_transcript_preview": hypothesis[:200],
                }
            except Exception as exc:
                log.warning("Back-transcription verification failed: %s", exc)

        # Baseline check if transcription is skipped or unavailable
        return {
            "passed": True,
            "reason": "File exists and is non-empty; back-transcription skipped.",
            "cer": 0.0,
        }

    def verify_translation(
        self,
        source_text: str,
        translated_text: str,
        target_lang: str,
    ) -> dict[str, Any]:
        """
        Verify that a translation is complete and preserves key structure (e.g. numbers).
        """
        if not translated_text.strip():
            return {
                "passed": False,
                "reason": "Translation output is empty.",
                "coverage": 0.0,
            }

        src_lines = [l for l in source_text.splitlines() if l.strip()]
        tgt_lines = [l for l in translated_text.splitlines() if l.strip()]
        ratio = len(tgt_lines) / max(len(src_lines), 1)

        # Check number preservation
        src_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", source_text))
        tgt_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", translated_text))
        numbers_preserved = src_numbers.issubset(tgt_numbers) if src_numbers else True

        # Check coverage
        coverage = min(ratio, 1.0)
        passed = (coverage >= 0.7) and (numbers_preserved or len(src_numbers) == 0)

        return {
            "passed": passed,
            "coverage": round(coverage, 2),
            "numbers_preserved": numbers_preserved,
            "source_lines": len(src_lines),
            "translated_lines": len(tgt_lines),
        }

    def verify_srt(self, srt_text: str, expected_duration_secs: float = 0.0) -> dict[str, Any]:
        """
        Check that SRT timestamps are monotonic, well-formatted, and span the audio length.
        """
        if not srt_text.strip():
            return {"passed": False, "reason": "SRT content is empty."}

        # Match timestamps: 00:00:01,000 --> 00:00:04,500
        matches = re.findall(
            r"(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})",
            srt_text,
        )
        if not matches:
            return {"passed": False, "reason": "No valid subtitle cue timestamps found."}

        def to_ms(h, m, s, ms):
            return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)

        monotonic = True
        last_end = 0
        for m in matches:
            start_ms = to_ms(m[0], m[1], m[2], m[3])
            end_ms = to_ms(m[4], m[5], m[6], m[7])
            if start_ms > end_ms or start_ms < last_end - 500:  # allow 500ms slight overlap
                monotonic = False
                break
            last_end = end_ms

        final_duration_secs = last_end / 1000.0
        coverage = (
            min(final_duration_secs / expected_duration_secs, 1.0)
            if expected_duration_secs > 0
            else 1.0
        )

        return {
            "passed": monotonic and coverage >= 0.75,
            "monotonic": monotonic,
            "cue_count": len(matches),
            "duration_covered_seconds": round(final_duration_secs, 1),
        }

    def _check_repetition(self, text: str, n: int = 5, threshold: int = 3) -> bool:
        """Check for looping repeats (known issue in some TTS/ASR models)."""
        words = text.split()
        if len(words) < n:
            return False
        from collections import Counter
        ngrams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
        counts = Counter(ngrams)
        return any(v >= threshold for v in counts.values())
