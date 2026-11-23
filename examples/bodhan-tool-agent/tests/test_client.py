"""Unit tests for HTTP client backoff, concurrency, and error handling (mocked offline)."""

import unittest
from unittest.mock import MagicMock, patch
import requests
from bodhan_mcp.client import BodhanAPIError, BodhanClient
from bodhan_mcp.config import Settings


class TestClient(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            bodhan_transcribe_key="mock_key",
            bodhan_translate_key="mock_key",
            bodhan_speak_key="mock_key",
            bodhan_ocr_key="mock_key",
            llm_base_url="https://llm.example.com/v1",
            llm_model="gpt-4o-mini",
            llm_api_key="mock_key",
        )
        self.client = BodhanClient(settings=self.settings, max_retries=2, backoff_factor=0.01)

    @patch("requests.Session.post")
    def test_client_success_200(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mock_post.return_value = mock_resp

        res = self.client.post("/test", model_id="indic-translate", json_data={"test": True})
        self.assertEqual(res.status_code, 200)

    @patch("requests.Session.post")
    def test_client_retry_on_429(self, mock_post):
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.text = "Rate limit exceeded"

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"status": "recovered"}

        mock_post.side_effect = [resp_429, resp_200]

        res = self.client.post("/test", model_id="indic-translate", json_data={"test": True})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(mock_post.call_count, 2)

    @patch("requests.Session.post")
    def test_client_fails_on_400(self, mock_post):
        resp_400 = MagicMock()
        resp_400.status_code = 400
        resp_400.text = "Bad Request: invalid language code"
        mock_post.return_value = resp_400

        with self.assertRaises(BodhanAPIError):
            self.client.post("/test", model_id="indic-translate", json_data={"test": True})


if __name__ == "__main__":
    unittest.main()
