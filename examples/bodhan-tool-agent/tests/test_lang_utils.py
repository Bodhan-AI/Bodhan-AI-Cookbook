"""Unit tests for language detection and validation."""

import unittest
from bodhan_mcp.lang_utils import (
    detect_script_language,
    extract_language_from_text,
    extract_task_languages,
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

    def test_language_extraction_rules(self):
        # 1. "say hi" gives no language
        code, name = extract_language_from_text("say hi")
        self.assertIsNone(code)
        self.assertIsNone(name)
        info1 = extract_task_languages("say hi")
        self.assertIsNone(info1["audio_language"])
        self.assertIsNone(info1["target_language"])

        # 2. "audio or video" gives no language
        code, name = extract_language_from_text("audio or video")
        self.assertIsNone(code)
        self.assertIsNone(name)
        info2 = extract_task_languages("audio or video")
        self.assertIsNone(info2["audio_language"])
        self.assertIsNone(info2["target_language"])

        # 3. "Transcribe Hindi audio and translate to Tamil" gives audio=hi and target=ta
        info3 = extract_task_languages("Transcribe Hindi audio and translate to Tamil")
        self.assertEqual(info3["audio_language"], "hi")
        self.assertEqual(info3["target_language"], "ta")
        self.assertFalse(info3["is_ambiguous"])

        # 4. "Create Hindi subtitles for this video" gives audio=hi and target=None
        info4 = extract_task_languages("Create Hindi subtitles for this video")
        self.assertEqual(info4["audio_language"], "hi")
        self.assertIsNone(info4["target_language"])

        # Explicit code forms
        code, _ = extract_language_from_text("transcribe in hi")
        self.assertEqual(code, "hi")
        code, _ = extract_language_from_text("transcribe language=hi")
        self.assertEqual(code, "hi")

        # Ambiguous tasks
        info_amb = extract_task_languages("Transcribe audio in Hindi or Tamil")
        self.assertIsNone(info_amb["audio_language"])
        self.assertIsNone(info_amb["target_language"])
        self.assertTrue(info_amb["is_ambiguous"])


if __name__ == "__main__":
    unittest.main()
