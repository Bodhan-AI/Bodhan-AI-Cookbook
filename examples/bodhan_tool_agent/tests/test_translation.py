import json
import unittest

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from bodhan_mcp.server import _translate_request


class TranslationTests(unittest.TestCase):
    def test_passes_explicit_target_and_returns_provider_usage(self):
        def handler(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(str(request.url), "https://api.bodhan.ai/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            self.assertEqual(
                json.loads(request.content),
                {
                    "model": "indic-translate",
                    "messages": [{"role": "user", "content": "There are 3 books."}],
                    "target_language_code": "kn",
                    "target_script": "native",
                },
            )
            return httpx.Response(200, json={
                "choices": [{
                    "message": {"content": "ಮೂರು ಪುಸ್ತಕಗಳಿವೆ."},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            })

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = _translate_request(client, "There are 3 books.", "kn", "test-key")
        self.assertEqual(result.translation, "ಮೂರು ಪುಸ್ತಕಗಳಿವೆ.")
        self.assertEqual(result.usage.completion_tokens, 8)

    def test_truncated_response_is_a_tool_error(self):
        response = httpx.Response(200, json={
            "choices": [{
                "message": {"content": "Partial output"},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 32, "completion_tokens": 4, "total_tokens": 36},
        })
        with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
            with self.assertRaisesRegex(ToolError, "incomplete_translation"):
                _translate_request(client, "ದೀರ್ಘ ಪಠ್ಯ", "en", "test-key")

    def test_rate_limit_does_not_expose_upstream_response_body(self):
        response = httpx.Response(429, text="Internal detail")
        with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
            with self.assertRaisesRegex(ToolError, r"^rate_limited: Wait before retrying\.$"):
                _translate_request(client, "Hello", "kn", "test-key")
