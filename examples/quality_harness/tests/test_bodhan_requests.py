import json

import httpx
import pytest

from bodhan_eval.cache import Cache
from bodhan_eval.clients.bodhan import BodhanSpeak, BodhanTranslate
from bodhan_eval.concurrency import ConcurrencyLimiter
from bodhan_eval.cost import CostLedger
from bodhan_eval.logging_utils import StructuredLogger


@pytest.mark.asyncio
async def test_hosted_request_payloads(tmp_path, monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append((url, kwargs["json"]))
            if url.endswith("/chat/completions"):
                return httpx.Response(200, json={"choices": [{"message": {"content": "नमस्ते"}}],
                                                 "usage": {"completion_tokens": 2}},
                                      request=httpx.Request("POST", url))
            return httpx.Response(200, content=b"wav", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    cache, ledger = Cache(tmp_path), CostLedger(max_inr=1)
    limiter, logger = ConcurrencyLimiter(2), StructuredLogger(None)
    translate = BodhanTranslate({"model": "indic-translate", "endpoint": "/chat/completions",
                                 "rate_inr_per_10k_output_tokens": .2},
                                "https://api.bodhan.ai/v1", "test", cache, ledger, limiter, logger)
    speak = BodhanSpeak({"model": "indic-speak", "endpoint": "/audio/speech",
                         "rate_inr_per_10k_input_chars": 6, "default_voice": "Kavya"},
                        "https://api.bodhan.ai/v1", "test", cache, ledger, limiter, logger)
    assert (await translate.translate("Hello", "en", "hi")).text == "नमस्ते"
    await speak.speak("नमस्ते", "hi")
    assert calls[0][0] == "https://api.bodhan.ai/v1/chat/completions"
    assert calls[0][1]["target_language"] == "hi"
    assert "temperature" not in calls[0][1] and "max_tokens" not in calls[0][1]
    assert json.loads(calls[1][1]["instructions"]) == {"lang": "hi"}
    assert calls[1][1]["voice"] == "Kavya"
