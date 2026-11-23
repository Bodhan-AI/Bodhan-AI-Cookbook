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
            llm_base_url="https://llm.example.com/v1",
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
        self.assertIn("LLM_BASE_URL", missing)
        self.assertIn("LLM_MODEL", missing)
        self.assertIn("LLM_API_KEY", missing)

    def test_llm_base_url_has_no_default_and_fails_validation_when_unset(self):
        """Tests that LLM_BASE_URL has no default value and fails validation when unset."""
        orig_env = dict(os.environ)
        try:
            # Unset all LLM variables
            os.environ.pop("LLM_BASE_URL", None)
            os.environ.pop("LLM_MODEL", None)
            os.environ.pop("LLM_API_KEY", None)

            from unittest.mock import patch
            with patch("dotenv.load_dotenv"):
                s = load_settings()
            self.assertEqual(s.llm_base_url, "")
            missing = validate_settings(s)
            self.assertIn("LLM_BASE_URL", missing)
        finally:
            os.environ.clear()
            os.environ.update(orig_env)

    def test_settings_timeout_and_retries_defaults_and_env(self):
        # Default values
        s = Settings("k1", "k2", "k3", "k4", "http://localhost", "model", "key")
        self.assertEqual(s.llm_timeout_s, 120.0)
        self.assertEqual(s.llm_max_retries, 1)

        # Custom values
        s_custom = Settings(
            "k1", "k2", "k3", "k4", "http://localhost", "model", "key",
            llm_timeout_s=45.0,
            llm_max_retries=2,
        )
        self.assertEqual(s_custom.llm_timeout_s, 45.0)
        self.assertEqual(s_custom.llm_max_retries, 2)


if __name__ == "__main__":
    unittest.main()
