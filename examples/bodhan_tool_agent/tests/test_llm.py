"""The OpenAI-compatible client: tool-call parsing, retries, clear setup errors."""
import json

import httpx
import pytest

from bodhan_agent.__main__ import main
from bodhan_agent.llm import LLM, LLMError


def _llm(handler):
    return LLM("http://llm.test/v1", "m", "k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _completion(message):
    return httpx.Response(200, json={"id": "1", "object": "chat.completion", "created": 0, "model": "m",
                                     "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
                                     "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}})


async def test_parses_tool_calls_and_tokens():
    def h(req):
        body = json.loads(req.content)
        assert body["tools"][0]["function"]["name"] == "t" and "temperature" not in body
        return _completion({"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{\"a\": 1}"}}]})
    r = await _llm(h).chat([{"role": "user", "content": "hi"}],
                           tools=[{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}])
    assert r.tool_calls[0].name == "t" and json.loads(r.tool_calls[0].arguments) == {"a": 1}
    assert (r.prompt_tokens, r.completion_tokens) == (5, 7)


async def test_retries_rate_limits_then_succeeds(monkeypatch):
    import asyncio
    real = asyncio.sleep
    monkeypatch.setattr("asyncio.sleep", lambda d, *a, **k: real(0))
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        return httpx.Response(429, json={"error": {"message": "slow down"}}) if n["i"] < 3 else _completion(
            {"role": "assistant", "content": "ok"})
    assert (await _llm(h).chat([{"role": "user", "content": "x"}])).content == "ok" and n["i"] == 3


async def test_hard_errors_become_llm_error():
    with pytest.raises(LLMError):
        await _llm(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}})).chat(
            [{"role": "user", "content": "x"}])


def test_missing_llm_settings_give_a_clear_message(monkeypatch, capsys, tmp_path):
    for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)                      # no .env here
    assert main(["--task", "x", "--out", str(tmp_path / "o")]) == 2
    assert "LLM_BASE_URL" in capsys.readouterr().err
