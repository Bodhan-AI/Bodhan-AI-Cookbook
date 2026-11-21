"""Unit tests for BodhanTranslationTool using mocked Bodhan client."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import httpx
import pytest
from pydantic import ValidationError

INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "integrations" / "langchain"
if str(INTEGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS_DIR))

from bodhan_langchain.client import BodhanAsyncClient, BodhanClient
from bodhan_langchain.tools import BodhanTranslationInput, BodhanTranslationTool

DUMMY_KEY = "dummy-tool-key"


def test_tool_metadata_and_schema() -> None:
    """Verify tool name, description, and Pydantic input schema."""
    tool = BodhanTranslationTool()
    assert tool.name == "bodhan_translate"
    assert "Indian languages" in tool.description
    assert tool.args_schema == BodhanTranslationInput

    # Valid schema instantiation
    valid_args = BodhanTranslationInput(text="Good morning", target_language="ta")
    assert valid_args.text == "Good morning"
    assert valid_args.target_language == "ta"
    assert valid_args.script == "native"

    # Missing required field raises validation error
    with pytest.raises(ValidationError):
        BodhanTranslationInput(target_language="ta")  # type: ignore[call-arg]


def test_tool_sync_invocation_success() -> None:
    """Verify synchronous tool invocation via .invoke()."""
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["target_language"] == "hi"
        if "messages" in data:
            assert data["messages"][0]["content"] == "Where is the library?"
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "पुस्तकालय कहाँ है?"}}],
            })
        assert data["text"] == "Where is the library?"
        return httpx.Response(200, json={
            "translation": "पुस्तकालय कहाँ है?",
        })

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    tool = BodhanTranslationTool(client=client)

    result = tool.invoke({"text": "Where is the library?", "target_language": "hi"})
    assert result == "पुस्तकालय कहाँ है?"


def test_tool_invalid_language_code() -> None:
    """Verify tool handles validation errors gracefully without crashing."""
    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client())
    tool = BodhanTranslationTool(client=client)

    result = tool.invoke({"text": "Hello", "target_language": "hi-IN"})
    assert "Error executing Bodhan translation" in result
    assert "plain ISO codes" in result


def test_tool_api_error_handling() -> None:
    """Verify tool handles API HTTP errors gracefully and returns descriptive message."""
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "Service Temporarily Unavailable", "code": "SERVICE_UNAVAILABLE"}})

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    tool = BodhanTranslationTool(client=client)

    result = tool.invoke({"text": "Hello", "target_language": "hi"})
    assert "Error executing Bodhan translation" in result
    assert "Service Temporarily Unavailable" in result


@pytest.mark.asyncio
async def test_tool_async_invocation() -> None:
    """Verify asynchronous tool invocation via .ainvoke()."""
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        if "messages" in data:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "Async Tool Output"}}],
            })
        return httpx.Response(200, json={
            "translation": "Async Tool Output",
        })

    async_client = BodhanAsyncClient(api_key=DUMMY_KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    tool = BodhanTranslationTool(async_client=async_client)

    result = await tool.ainvoke({"text": "Test async tool", "target_language": "bn"})
    assert result == "Async Tool Output"


def test_tool_default_target_language_in_constructor() -> None:
    """Verify that target_language can be passed to tool constructor and used by default."""
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert data["target_language"] == "ta"
        return httpx.Response(200, json={"translation": "வணக்கம்"})

    client = BodhanClient(api_key=DUMMY_KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.bodhan.ai"))
    tool = BodhanTranslationTool(client=client, target_language="ta")

    # Invocations without explicit target_language inherit constructor default
    result = tool.invoke({"text": "Hello"})
    assert result == "வணக்கம்"


def test_tool_internal_client_lifecycle_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that internally instantiated client is closed after tool run."""
    closed = False

    class MockInternalClient:
        def __init__(self, *args, **kwargs):
            pass
        def translate(self, *args, **kwargs):
            from bodhan_langchain.models import TranslationResponse
            return TranslationResponse(text="Tr Text", target_language="hi")
        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr("bodhan_langchain.tools.BodhanClient", MockInternalClient)

    tool = BodhanTranslationTool()
    res = tool.invoke({"text": "Test"})
    assert res == "Tr Text"
    assert closed is True
