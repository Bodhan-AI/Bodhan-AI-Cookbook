from __future__ import annotations

import json

import pytest

from bodhan_dub.config import LLMConfig
from bodhan_dub.llm import LLMClient


class FakeResponse:
    def __init__(self, status: int, *, body=None, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.text = text
        self.headers: dict[str, str] = {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    def __init__(self, answers: list[str]) -> None:
        self._answers = answers
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        content = self._answers.pop(0) if self._answers else ""
        return FakeResponse(
            200, body={"choices": [{"message": {"role": "assistant", "content": content}}]}
        )


def _client(answers: list[str]) -> LLMClient:
    config = LLMConfig(base_url="https://llm.test/v1", model="test-model", api_key="k")
    return LLMClient(config, session=FakeSession(answers))


def test_rejects_a_non_latin_answer() -> None:
    # A model that answers in Hindi must never be accepted: only Indic-Translate
    # may produce Indian-language text.
    reason = LLMClient._rejection_reason("a fairly long English sentence", "यह छोटा है", 20, [])

    assert reason is not None
    assert "Latin" in reason


def test_rejects_a_dropped_number() -> None:
    reason = LLMClient._rejection_reason(
        "the angle is 90 degrees and it matters", "the angle is small", 20, []
    )

    assert reason is not None
    assert "90" in reason


def test_rejects_a_dropped_protected_term() -> None:
    reason = LLMClient._rejection_reason(
        "a right angle is ninety degrees exactly here",
        "the angle is ninety degrees exactly",
        20,
        ["right angle"],
    )

    assert reason is not None
    assert "right angle" in reason


def test_rejects_an_answer_that_is_not_shorter() -> None:
    text = "the same length answer here"
    reason = LLMClient._rejection_reason(text, text, 100, [])

    assert reason is not None
    assert "shorter" in reason


def test_rejects_an_answer_that_is_too_long() -> None:
    reason = LLMClient._rejection_reason(
        "a very long sentence that needs shortening quite a lot",
        "x" * 200,
        20,
        [],
    )

    assert reason is not None


def test_accepts_a_good_answer() -> None:
    assert LLMClient._rejection_reason("a very long sentence here", "a short one", 20, []) is None


def test_condense_returns_the_answer() -> None:
    client = _client(["angles under 90 degrees"])

    result = client.condense("an acute angle is an angle that is less than ninety degrees", target_chars=30)

    assert result == "angles under 90 degrees"
    body = client._session.calls[0]["json"]
    assert body["model"] == "test-model"
    assert body["temperature"] == 0
    assert "English only" in body["messages"][0]["content"]


def test_condense_rejects_an_unsafe_answer() -> None:
    client = _client(["यह बहुत छोटा है"])

    assert client.condense("a fairly long English sentence about angles", target_chars=20) is None


def test_condense_skips_text_that_is_already_short() -> None:
    client = _client([])

    assert client.condense("short", target_chars=50) is None
    assert client._session.calls == []


def test_condense_sends_the_target_length() -> None:
    client = _client(["a short answer"])

    client.condense("a much longer english sentence that must shrink", target_chars=41)

    prompt = client._session.calls[0]["json"]["messages"][1]["content"]
    assert "41" in prompt


def test_http_failure_is_swallowed_as_none() -> None:
    class Failing:
        def post(self, url, **kwargs):
            return FakeResponse(400, body=None, text=json.dumps({"error": "bad"}))

    config = LLMConfig(base_url="https://llm.test/v1", model="m", api_key="k")
    client = LLMClient(config, session=Failing())

    assert client.condense("a long english sentence to shorten", target_chars=20) is None


def test_disabled_config_is_reported() -> None:
    config = LLMConfig(base_url="", model="", api_key="")
    assert LLMClient(config).enabled is False


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_input_is_ignored(value: str) -> None:
    client = _client([])
    assert client.condense(value, target_chars=10) is None
