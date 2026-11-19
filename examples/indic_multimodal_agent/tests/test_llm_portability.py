"""Tests for LLM provider portability and configuration.
Validates that Gemini-specific metadata (extra_content, thought_signature) is preserved
for Google Gemini endpoints, but stripped for standard OpenAI-compatible endpoints,
and that missing LLM_BASE_URL raises a clear ValueError.
"""

import pytest
from bodhan_agent.llm import LLMClient
from bodhan_mcp.config import Settings


def test_sanitize_messages_openai_endpoint():
    client = LLMClient(
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        api_key="sk-mock",
    )
    raw_messages = [
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "content": None,
            "extra_content": {"google": {"thought_signature": "sig123"}},
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                    "extra_content": {"google": {"thought_signature": "sig456"}},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ]

    sanitized = client.sanitize_messages(raw_messages)
    assert len(sanitized) == 3
    # Check extra_content stripped from assistant message
    assert "extra_content" not in sanitized[1]
    # Check extra_content stripped from tool call
    assert "extra_content" not in sanitized[1]["tool_calls"][0]
    assert sanitized[1]["tool_calls"][0]["id"] == "call_1"


def test_sanitize_messages_gemini_endpoint():
    client = LLMClient(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-flash-lite-latest",
        api_key="mock-key",
    )
    raw_messages = [
        {
            "role": "assistant",
            "content": None,
            "extra_content": {"google": {"thought_signature": "sig123"}},
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "test_tool", "arguments": "{}"}}],
        }
    ]

    sanitized = client.sanitize_messages(raw_messages)
    assert len(sanitized) == 1
    # Preserved for Gemini
    assert sanitized[0].get("extra_content") == {"google": {"thought_signature": "sig123"}}


def test_missing_llm_base_url_raises_clear_error(monkeypatch):
    """Confirm that when LLM_BASE_URL is not provided, config raises ValueError with a clear error message."""
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    test_settings = Settings()
    with pytest.raises(ValueError, match="LLM_BASE_URL is required"):
        _ = test_settings.LLM_BASE_URL
