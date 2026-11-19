"""Tests for LLM provider portability.
Validates that Gemini-specific metadata (extra_content, thought_signature) is preserved
for Google Gemini endpoints, but stripped for standard OpenAI-compatible endpoints.
"""

from bodhan_agent.llm import LLMClient


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

