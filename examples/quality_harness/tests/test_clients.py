import io
import json
import wave
from pathlib import Path

import httpx2
import pytest

from bodhan_eval.clients import LLM_RETRIES, BudgetExceeded, Clients, ModelError, parse_json_object, wav_seconds
from bodhan_eval.config import Config

FIXTURES = Path(__file__).parent / "fixtures" / "bodhan"
KEYS = {"translate": "secret-t", "speak": "secret-s", "transcribe": "secret-a"}


def silent_wav(seconds: float, rate: int = 24_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(seconds * rate))
    return buf.getvalue()


def llm_reply(content, finish="stop"):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class FakeServer:
    """Answers requests from a queue of (status, body, headers) per path, and records every request."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status, body, headers = self.routes[request.url.path].pop(0)
        if isinstance(body, bytes):
            return httpx2.Response(status, content=body, headers=headers)
        return httpx2.Response(status, json=body, headers=headers)


def make(tmp_path, routes, cap=None, llm=False):
    server = FakeServer(routes)
    cfg = Config(dataset=Path("d"), langs=("hi",), out=Path("o"), max_cost_inr=cap, bodhan_keys=KEYS,
                 llm_base_url="https://llm.test/v1" if llm else None, llm_model="m", llm_api_key="secret-l")
    clients = Clients(cfg, tmp_path / "cache", http_client=httpx2.Client(transport=httpx2.MockTransport(server)))
    return clients, server


TRANSLATE_OK = (200, json.loads((FIXTURES / "translate_hi.json").read_text()), {})


def test_translate_sends_target_and_is_cached(tmp_path):
    clients, server = make(tmp_path, {"/v1/chat/completions": [TRANSLATE_OK]})
    first = clients.translate("Water boils at 100 °C.", "hi")
    again = clients.translate("Water boils at 100 °C.", "hi")  # no second response queued: must come from cache
    assert first == again and first.text.startswith("समुद्र") and first.finish_reason == "stop"

    body = json.loads(server.requests[0].content)
    assert body["target_language_code"] == "hi" and body["model"] == "indic-translate"
    assert "temperature" not in body  # Bodhan rejects it
    assert server.requests[0].headers["authorization"] == "Bearer secret-t"

    s = clients.ledger.summary()["by_model"]["translate"]
    assert s["calls"] == 1 and s["cache_hits"] == 1 and s["cost_inr"] > 0 and s["cached_cost_inr"] == s["cost_inr"]
    clients.close()
    assert b"secret" not in (tmp_path / "cache" / "cache.sqlite").read_bytes()


def test_rate_limit_is_retried(tmp_path):
    clients, server = make(tmp_path, {"/v1/chat/completions": [
        (429, {"error": {"message": "slow down"}}, {"retry-after-ms": "1"}), TRANSLATE_OK]})
    assert clients.translate("Hi.", "hi").text
    assert len(server.requests) == 2


def test_failure_raises_model_error_and_is_not_cached(tmp_path):
    error = json.loads((FIXTURES / "error_unsupported_language.json").read_text())
    clients, server = make(tmp_path, {"/v1/chat/completions": [(400, error, {}), TRANSLATE_OK]})
    with pytest.raises(ModelError, match="HTTP 400"):
        clients.translate("Hi.", "xx")
    assert clients.ledger.stats["translate"].failures == 1
    with pytest.raises(ModelError, match="no credentials"):
        clients.llm([{"role": "user", "content": "x"}])


def test_budget_blocks_paid_calls_but_not_cache_hits(tmp_path):
    clients, server = make(tmp_path, {"/v1/chat/completions": [TRANSLATE_OK]}, cap=0.001)
    clients.translate("Hi.", "hi")  # about ₹0.0003: fits
    with pytest.raises(BudgetExceeded):
        clients.speak("x" * 100, "hi")  # ₹0.06: does not
    assert clients.translate("Hi.", "hi").text  # cached, free
    assert len(server.requests) == 1
    assert clients.ledger.stats["speak"].skipped_budget == 1


def test_speak_then_transcribe(tmp_path):
    transcript = json.loads((FIXTURES / "transcribe_hi.json").read_text())
    clients, server = make(tmp_path, {
        "/v1/audio/speech": [(200, silent_wav(6.0), {"content-type": "audio/mpeg"})],  # Bodhan mislabels WAV
        "/v1/audio/transcriptions": [(200, transcript, {})],
    })
    wav = clients.speak("नमस्ते", "hi")
    assert wav.is_file() and wav_seconds(wav) == 6.0
    body = json.loads(server.requests[0].content)
    assert body["voice"] == "Kavya" and json.loads(body["instructions"]) == {"lang": "hi"}

    assert clients.transcribe(wav, "hi") == transcript["text"]
    assert clients.transcribe(wav, "hi") == transcript["text"]  # keyed by the audio bytes: cached
    form = server.requests[1].content
    assert b'name="model"' in form and b"indic-transcribe" in form and b'name="language"' in form
    assert clients.ledger.stats["transcribe"].cost_inr == pytest.approx(0.01)  # 6 s at ₹0.10/min


def test_llm_retries_empty_reply_and_parses_messy_json(tmp_path):
    clients, server = make(tmp_path, {"/v1/chat/completions": [
        (200, llm_reply("", finish="length"), {}),  # all tokens spent on reasoning
        (200, llm_reply('Here you go:\n```json\n{"errors": [{"span": "{x}"}]}}\n```'), {}),
    ]}, llm=True)
    msgs = [{"role": "user", "content": "judge this"}]
    assert clients.llm(msgs) == {"errors": [{"span": "{x}"}]}
    assert clients.llm(msgs) == {"errors": [{"span": "{x}"}]}  # cached
    body = json.loads(server.requests[0].content)
    assert body["max_tokens"] == 4096 and not {"temperature", "seed", "response_format"} & body.keys()
    s = clients.ledger.stats["llm"]
    assert len(server.requests) == 2 and s.tokens == 30 and s.calls == 1 and s.cache_hits == 1


def test_llm_gives_up_after_retries(tmp_path):
    clients, _ = make(tmp_path, {"/v1/chat/completions": [(200, llm_reply("no json here"), {})] * (LLM_RETRIES + 1)}, llm=True)
    with pytest.raises(ModelError, match="no JSON"):
        clients.llm([{"role": "user", "content": "x"}])


@pytest.mark.parametrize(
    "text, expected",
    [('{"a": 1}', {"a": 1}), ('{"a": 1}}', {"a": 1}), ('note {bad} then {"a": [1]} end', {"a": [1]}),
     ("[1, 2]", None), ("", None), ('{"a": ', None)],
)
def test_parse_json_object(text, expected):
    assert parse_json_object(text) == expected


def test_rate_limit_after_sdk_retries_pauses_and_recovers(tmp_path, monkeypatch):
    import bodhan_eval.clients as c
    monkeypatch.setattr(c, "_retry_after", lambda e: 0.01)
    limited = (429, {"error": {"message": "Maximum rate limit reached for the model"}}, {"retry-after-ms": "1"})
    clients, server = make(tmp_path, {"/v1/chat/completions": [limited] * (c.SDK_RETRIES + 1) + [TRANSLATE_OK]})
    assert clients.translate("Hi.", "hi").text  # survives the SDK giving up once
    assert len(server.requests) == c.SDK_RETRIES + 2


def test_out_of_credit_is_not_waited_on(tmp_path, monkeypatch):
    import bodhan_eval.clients as c
    monkeypatch.setattr(c, "_retry_after", lambda e: 0.01)
    broke = (429, {"error": {"message": "Insufficient credit balance"}}, {"retry-after-ms": "1"})
    clients, server = make(tmp_path, {"/v1/chat/completions": [broke] * (c.SDK_RETRIES + 1)})
    with pytest.raises(ModelError):
        clients.translate("Hi.", "hi")
    assert len(server.requests) == c.SDK_RETRIES + 1
