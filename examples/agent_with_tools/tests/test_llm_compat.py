import json

import httpx
import pytest

from bodhan_agent.llm_compat import ToolCallExtrasTransport


async def test_tool_call_extra_content_is_sent_back_on_the_next_request():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        call = {"id": "call_1", "type": "function", "function": {"name": "t", "arguments": "{}"}, "extra_content": {"google": {"thought_signature": "SIG"}}}
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]})

    async with httpx.AsyncClient(transport=ToolCallExtrasTransport(httpx.MockTransport(handler))) as client:
        first = (await client.post("http://x/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})).json()
        assert first["choices"][0]["message"]["tool_calls"][0]["extra_content"]  # the caller still sees the response unchanged
        assistant = {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "t", "arguments": "{}"}}]}
        await client.post("http://x/chat/completions", json={"messages": [{"role": "user", "content": "hi"}, assistant]})
    assert seen[1]["messages"][1]["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "SIG"}}


async def test_requests_pass_through_unchanged_when_provider_sends_no_extras():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    async with httpx.AsyncClient(transport=ToolCallExtrasTransport(httpx.MockTransport(handler))) as client:
        await client.post("http://x/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
        await client.post("http://x/chat/completions", json={"messages": [{"role": "user", "content": "again"}]})
    assert json.loads(sent[1])["messages"][0]["content"] == "again"
