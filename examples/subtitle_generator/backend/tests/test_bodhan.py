"""Bodhan calls, tested against a fake HTTP server (httpx.MockTransport); no key or network needed."""

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app import bodhan
from app.config import Settings, settings

# Fixed settings for these tests, so a developer's own .env (mock mode, per-model keys) can't change the results
LIVE = {
    "dev_mock_mode": False,
    "bodhan_api_key": "test-key",
    "bodhan_transcribe_api_key": "",
    "bodhan_translate_api_key": "",
    "bodhan_base_url": "https://api.bodhan.ai",
    "bodhan_timestamps": False,
}


def run(coro_fn, handler):
    """Run coro_fn(client) with an httpx client whose requests go to handler instead of the network."""

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await coro_fn(client)

    return asyncio.run(main())


class BodhanTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(settings, name, value) for name, value in LIVE.items()]
        for p in self.patches:
            p.start()
        bodhan._translate_route_missing = False

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_an_empty_model_key_falls_back_to_the_shared_key(self):
        with patch.dict(os.environ, {"BODHAN_API_KEY": "shared", "BODHAN_TRANSLATE_API_KEY": "tr"}, clear=True):
            config = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]  # a pydantic-settings init option
        self.assertEqual((config.transcribe_key, config.translate_key), ("shared", "tr"))

    def test_missing_key_is_a_503(self):
        with patch.object(settings, "bodhan_api_key", ""), self.assertRaises(bodhan.BodhanError) as caught:
            run(lambda c: bodhan.translate(c, "नमस्ते", "en"), lambda r: httpx.Response(200))
        self.assertEqual(caught.exception.status, 503)

    def test_transcribe_request(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"text": "बोधन"})

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"RIFF....WAVE")
        try:
            transcript = run(lambda c: bodhan.transcribe(c, f.name, "hi"), handler)
        finally:
            os.remove(f.name)
        self.assertEqual(transcript.text, "बोधन")
        self.assertIsNone(transcript.words)
        self.assertEqual(str(seen[0].url), "https://api.bodhan.ai/v1/audio/transcriptions")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer test-key")
        body = seen[0].content.decode("latin1")
        self.assertIn('name="model"\r\n\r\nindic-transcribe', body)
        self.assertIn('name="language"\r\n\r\nhi', body)
        self.assertNotIn("response_format", body)  # timestamps are off by default

    def test_transcribe_with_timestamps(self):
        replies = [
            # What the live API returns today: the OpenAI verbose_json shape with empty timings
            {"text": "नमस्ते", "words": None, "segments": None},
            # What it could return once Bodhan supports timestamps
            {"text": "नमस्ते दोस्तों", "words": [{"word": "नमस्ते", "start": 0.0, "end": 0.5}, {"word": "दोस्तों", "start": 0.6, "end": 1.0}, {"word": "bad"}]},
        ]
        bodies = []

        def handler(request):
            bodies.append(request.content.decode("latin1"))
            return httpx.Response(200, json=replies.pop(0))

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"RIFF....WAVE")
        try:
            with patch.object(settings, "bodhan_timestamps", True):
                today = run(lambda c: bodhan.transcribe(c, f.name, "hi"), handler)
                later = run(lambda c: bodhan.transcribe(c, f.name, "hi"), handler)
        finally:
            os.remove(f.name)
        self.assertIn('name="response_format"\r\n\r\nverbose_json', bodies[0])
        self.assertNotIn("timestamp_granularities", bodies[0])  # the live API rejects this field
        self.assertEqual((today.text, today.words, today.segments), ("नमस्ते", None, None))
        assert later.words is not None
        self.assertEqual([w["word"] for w in later.words], ["नमस्ते", "दोस्तों"])  # the malformed item is skipped

    def test_translate_request(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://api.bodhan.ai/translate")
            self.assertEqual(json.loads(request.content), {"text": "नमस्ते", "target_language": "en", "script": "native"})
            return httpx.Response(200, json={"translation": "Hello", "truncated": False})

        self.assertEqual(run(lambda c: bodhan.translate(c, "नमस्ते", "en"), handler), "Hello")

    def test_translate_falls_back_to_chat_and_remembers(self):
        urls = []

        def handler(request):
            urls.append(request.url.path)
            if request.url.path == "/translate":
                return httpx.Response(404, json={"detail": "Not Found"})
            body = json.loads(request.content)
            self.assertEqual((body["model"], body["target_language_code"]), ("indic-translate", "en"))
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Hello"}}]})

        async def twice(client):
            return [await bodhan.translate(client, "नमस्ते", "en"), await bodhan.translate(client, "नमस्ते", "en")]

        self.assertEqual(run(twice, handler), ["Hello", "Hello"])
        self.assertEqual(urls, ["/translate", "/v1/chat/completions", "/v1/chat/completions"])

    def test_rate_limit_is_retried(self):
        responses = [httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"translation": "Hi"})]
        self.assertEqual(run(lambda c: bodhan.translate(c, "नमस्ते", "en"), lambda r: responses.pop(0)), "Hi")

    def test_http_error_is_readable(self):
        with self.assertRaisesRegex(bodhan.BodhanError, "HTTP 403"):
            run(lambda c: bodhan.translate(c, "नमस्ते", "en"), lambda r: httpx.Response(403, text="key_model_access_denied"))


if __name__ == "__main__":
    unittest.main()
