"""Output verification module: verifies audio and translations before completing tasks."""

from __future__ import annotations

import contextlib
import wave
from pathlib import Path
from typing import Dict, Optional, Tuple

from bodhan_mcp.client.client import BodhanClient, SUPPORTED_LANGUAGES, normalize_language_code
from bodhan_mcp.tools.verify_and_subtitles import bodhan_verify_speech, bodhan_verify_translation


class Verifier:
    """Verifies generated speech, translations, and OCR documents."""

    def __init__(self, client: Optional[BodhanClient] = None):
        self.client = client or BodhanClient()
        self.last_cost_inr: float = 0.0

    def verify_audio(
        self,
        audio_path: str,
        expected_text: str,
        language: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Verify generated audio by checking duration, validity, and back-transcribing."""
        self.last_cost_inr = 0.0
        try:
            res = bodhan_verify_speech(
                audio_file=audio_path,
                expected_text=expected_text,
                language=language,
                client=self.client,
            )
            self.last_cost_inr = float(res.get("cost_inr", 0.0))
            if res.get("passed"):
                return (
                    True,
                    f"Audio valid (duration: {res.get('duration_seconds', 0):.2f}s, "
                    f"overlap: {res.get('word_overlap_ratio', 0):.2f}, "
                    f"len_ratio: {res.get('length_ratio', 0):.2f}).",
                )
            reason = res.get("reason") or "; ".join(res.get("issues", [])) or "Speech verification failed."
            return False, reason
        except Exception as e:
            return False, f"Audio verification failed: {str(e)}"

    def verify_translation(
        self,
        source_text: str,
        translation_text: str,
        target_language: str,
        script: str = "native",
    ) -> Tuple[bool, str]:
        """Verify translation covers the source, is non-empty, and matches target script."""
        self.last_cost_inr = 0.0
        target_lang = normalize_language_code(target_language)
        res = bodhan_verify_translation(
            source_text=source_text,
            translated_text=translation_text,
            target_language=target_lang,
            script=script,
            back_translate=False,
            client=self.client,
        )
        if res.get("passed"):
            return True, f"Translation complete ({len(translation_text.strip())} chars, target '{target_lang}')."
        return False, "; ".join(res.get("issues", ["Translation verification failed."]))

    def verify_ocr(self, markdown_text: str, min_chars: int = 5) -> Tuple[bool, str]:
        """Verify OCR extraction extracted meaningful text."""
        clean = markdown_text.strip()
        if len(clean) < min_chars:
            return False, f"OCR returned insufficient text ({len(clean)} characters)."
        return True, f"OCR successfully extracted document layout and text ({len(clean)} characters)."
