"""Unit tests for language detection and validation."""

import unittest
from bodhan_mcp.lang_utils import (
    detect_script_language,
    extract_language_from_text,
    is_language_supported,
)


class TestLangUtils(unittest.TestCase):
    def test_is_language_supported_valid(self):
        # Supported ISO codes
        valid_codes = ["hi", "ta", "te", "bn", "mr", "gu", "en", "ur"]
        for c in valid_codes:
            supported, code, _ = is_language_supported(c)
            self.assertTrue(supported)
            self.assertEqual(code, c)

        # Supported English names
        supported, code, _ = is_language_supported("Tamil")
        self.assertTrue(supported)
        self.assertEqual(code, "ta")

        supported, code, _ = is_language_supported("Hindi")
        self.assertTrue(supported)
        self.assertEqual(code, "hi")

    def test_is_language_supported_unsupported(self):
        # French, German, Spanish must fail honestly
        unsupported = ["French", "fr", "German", "de", "Spanish", "es", "Japanese", "ja"]
        for u in unsupported:
            supported, code, msg = is_language_supported(u)
            self.assertFalse(supported)
            self.assertIsNone(code)
            self.assertIn("not supported", msg)

    def test_detect_script_language(self):
        # Devanagari
        self.assertEqual(detect_script_language("नमस्ते आप कैसे हैं?"), "hi")

        # Tamil
        self.assertEqual(detect_script_language("வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்?"), "ta")

        # Telugu
        self.assertEqual(detect_script_language("నమస్కారం, మీరు ఎలా ఉన్నారు?"), "te")

        # Bengali
        self.assertEqual(detect_script_language("নমস্কার, আপনি কেমন আছেন?"), "bn")

        # English (Latin)
        self.assertEqual(detect_script_language("The school library opens at nine."), "en")

    def test_extract_language_from_text(self):
        # Explicit language names
        code, name = extract_language_from_text("Transcribe this Hindi voice note and save")
        self.assertEqual(code, "hi")
        self.assertEqual(name, "Hindi")

        code, name = extract_language_from_text("Make a Tamil summary of this audio")
        self.assertEqual(code, "ta")
        self.assertEqual(name, "Tamil")

        # No language in prompt
        code, name = extract_language_from_text("Transcribe this audio file and save the transcript")
        self.assertIsNone(code)
        self.assertIsNone(name)


if __name__ == "__main__":
    unittest.main()
