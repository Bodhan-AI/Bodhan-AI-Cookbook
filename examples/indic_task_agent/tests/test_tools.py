import unittest
from unittest.mock import patch

from indic_task_agent.bodhan_tools import BodhanError, _request, indic_asr, indic_translate
from indic_task_agent.config import Settings

S = Settings({"indic-translate": "dummy"}, "https://api.bodhan.ai", "https://llm.example/v1", "mock", "dummy", 2, 1.0, 1, 0)


class ToolTests(unittest.TestCase):
    def test_french_is_rejected_before_http_request(self):
        with patch("indic_task_agent.bodhan_tools._request") as request:
            with self.assertRaises(BodhanError):
                indic_translate(S, "Hello", "fr")
            request.assert_not_called()

    def test_missing_audio_is_handled_without_http_request(self):
        with patch("indic_task_agent.bodhan_tools._request") as request:
            with self.assertRaises(BodhanError):
                indic_asr(S, "not-a-real-audio-file.wav")
            request.assert_not_called()

    def test_missing_model_key_stops_before_http_request(self):
        with patch("indic_task_agent.bodhan_tools.requests.request") as request:
            with self.assertRaisesRegex(BodhanError, "BODHAN_OCR_KEY"):
                _request(S, "indic-ocr", "POST", "/v1/chat/completions")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
