"""Offline unit tests for standalone MCP server tool registry without LLM configuration."""

import os
import unittest
from unittest.mock import patch

from bodhan_mcp.config import load_settings, validate_settings


class TestMCPServerStandalone(unittest.TestCase):
    def test_mcp_server_lists_eight_tools_with_only_bodhan_keys_and_no_llm_keys(self):
        """Tests that the MCP server's tool registry lists all 8 tools with ONLY
        the four BODHAN_*_KEY variables set and NO LLM_* variables configured,
        with .env loading disabled.
        """
        orig_env = dict(os.environ)
        try:
            # Strip all LLM_* variables from environment
            for k in list(os.environ.keys()):
                if k.startswith("LLM_"):
                    os.environ.pop(k, None)

            # Set ONLY the four Bodhan model keys
            os.environ["BODHAN_TRANSCRIBE_KEY"] = "mock_transcribe_key"
            os.environ["BODHAN_TRANSLATE_KEY"] = "mock_translate_key"
            os.environ["BODHAN_SPEAK_KEY"] = "mock_speak_key"
            os.environ["BODHAN_OCR_KEY"] = "mock_ocr_key"

            # Verify no LLM variables exist in environment
            self.assertNotIn("LLM_BASE_URL", os.environ)
            self.assertNotIn("LLM_MODEL", os.environ)
            self.assertNotIn("LLM_API_KEY", os.environ)

            # Disable .env loading for this test
            with patch("dotenv.load_dotenv"):
                # 1. Verify MCP settings validation succeeds with only the four keys
                settings = load_settings()
                missing = validate_settings(settings)
                self.assertEqual(missing, [], f"Expected 0 missing keys for MCP server, got: {missing}")

                # 2. Import server object in-process and inspect its tool registry
                import importlib
                import bodhan_mcp.server as server_mod
                importlib.reload(server_mod)

                server_mcp = server_mod.mcp
                registered_tools = list(server_mcp._tool_manager._tools.keys())

            # 3. Assert the registry lists exactly the 8 expected tools
            expected_tools = {
                "transcribe_audio",
                "translate_text",
                "synthesize_speech",
                "extract_document_text",
                "inspect_media",
                "detect_language",
                "extract_audio_from_video",
                "build_srt",
            }
            self.assertEqual(len(registered_tools), 8)
            self.assertEqual(set(registered_tools), expected_tools)

        finally:
            os.environ.clear()
            os.environ.update(orig_env)


if __name__ == "__main__":
    unittest.main()
