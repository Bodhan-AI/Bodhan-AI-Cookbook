"""Unit tests for configuration loading and validation."""

import os
import unittest
from bodhan_mcp.config import Settings, load_settings, validate_settings


class TestConfig(unittest.TestCase):
    def test_settings_get_model_key(self):
        s = Settings(
            bodhan_transcribe_key="key_transcribe",
            bodhan_translate_key="key_translate",
            bodhan_speak_key="key_speak",
            bodhan_ocr_key="key_ocr",
            llm_base_url="https://api.openai.com/v1",
            llm_model="gpt-4o-mini",
            llm_api_key="key_llm",
        )
        self.assertEqual(s.get_model_key("indic-transcribe"), "key_transcribe")
        self.assertEqual(s.get_model_key("indic-translate"), "key_translate")
        self.assertEqual(s.get_model_key("indic-speak"), "key_speak")
        self.assertEqual(s.get_model_key("indic-ocr"), "key_ocr")

        with self.assertRaises(ValueError):
            s.get_model_key("non-existent-model")

    def test_validate_settings_missing_keys(self):
        empty_s = Settings("", "", "", "", "", "", "")
        missing = validate_settings(empty_s)
        self.assertIn("BODHAN_TRANSCRIBE_KEY", missing)
        self.assertIn("BODHAN_TRANSLATE_KEY", missing)
        self.assertIn("BODHAN_SPEAK_KEY", missing)
        self.assertIn("BODHAN_OCR_KEY", missing)


if __name__ == "__main__":
    unittest.main()
