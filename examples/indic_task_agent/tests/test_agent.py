import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from indic_task_agent.agent import TOOL_NAMES, _supported_task_guard, _verify, run_task
from indic_task_agent.config import Settings


def settings():
    return Settings("dummy", "https://api.bodhan.ai", "https://llm.example/v1", "mock", "dummy", 3, 2.0, 1, 0)


class AgentTests(unittest.TestCase):
    def test_only_four_bodhan_tools_are_exposed(self):
        self.assertEqual(TOOL_NAMES, {"indic_asr", "indic_translate", "indic_tts", "indic_ocr"})

    def test_french_translation_is_refused_before_planning(self):
        self.assertIn("Unsupported", _supported_task_guard("Translate this into French"))

    def test_unrelated_task_is_not_rejected_by_guard(self):
        self.assertIsNone(_supported_task_guard("Read this Hindi page"))

    def test_french_source_can_be_translated_to_supported_target(self):
        self.assertIsNone(_supported_task_guard("Translate this French sentence into Hindi"))

    def test_empty_text_fails_verification(self):
        with self.assertRaises(ValueError):
            _verify("indic_translate", {"text": "  "})

    def test_french_task_writes_required_artifacts_without_llm_or_bodhan(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("indic_task_agent.agent.OpenAI") as llm:
                result = run_task("Translate this into French", settings(), Path(temp))
                llm.assert_not_called()
            self.assertEqual(result["status"], "unsupported")
            self.assertTrue(Path(result["trace"]).is_file())
            self.assertTrue(Path(result["report"]).is_file())
            self.assertTrue(Path(result["result"]).is_file())
            self.assertEqual(json.loads(Path(result["report"]).read_text())["steps"], [])

    def test_trace_redaction_keeps_local_references_out(self):
        from indic_task_agent.agent import _redact_paths

        self.assertEqual(_redact_paths({"audio_path": "C:/private/clip.wav"}), {"audio_path": "<local-file-reference>"})


if __name__ == "__main__":
    unittest.main()
