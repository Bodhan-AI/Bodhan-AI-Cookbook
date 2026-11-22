"""Unit tests for OpenAI-compatible LLM helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bodhan_lesson.llm_client import _extract_json_object, chat_json, normalize_usage


def test_extract_json_object_plain() -> None:
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_fenced() -> None:
    text = 'Sure.\n```json\n{"ordered_ids": ["0"]}\n```\n'
    assert _extract_json_object(text) == {"ordered_ids": ["0"]}


def test_normalize_usage() -> None:
    assert normalize_usage(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    ) == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert normalize_usage({"input_tokens": 3, "output_tokens": 4}) == {
        "prompt_tokens": 3,
        "completion_tokens": 4,
        "total_tokens": 7,
    }


def test_chat_json_parses_response_and_usage() -> None:
    fake = MagicMock()
    fake.ok = True
    fake.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
    }
    with patch("bodhan_lesson.llm_client.requests.post", return_value=fake):
        with patch("bodhan_lesson.llm_client.get_llm_api_key", return_value="k"):
            data, usage = chat_json([{"role": "user", "content": "hi"}])
    assert data == {"ok": True}
    assert usage == {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13}


def test_chat_json_raises_on_http_error() -> None:
    fake = MagicMock()
    fake.ok = False
    fake.status_code = 401
    fake.text = "unauthorized"
    with patch("bodhan_lesson.llm_client.requests.post", return_value=fake):
        with patch("bodhan_lesson.llm_client.get_llm_api_key", return_value="k"):
            with pytest.raises(RuntimeError, match="401"):
                chat_json([{"role": "user", "content": "hi"}])
