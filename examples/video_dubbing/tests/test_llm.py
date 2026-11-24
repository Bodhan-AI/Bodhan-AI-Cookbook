"""LLM wrapper against a fake OpenAI client: optional-setting fallback, JSON re-asks, empty replies."""

from types import SimpleNamespace

import httpx2 as httpx  # the HTTP library openai 3.x builds its errors on
import pytest
from openai import BadRequestError

from bodhan_dub.config import Settings
from bodhan_dub.llm import LLM, LLMError

ENV = {"LLM_BASE_URL": "http://llm", "LLM_MODEL": "m", "LLM_API_KEY": "k",
       "LLM_REASONING_EFFORT": "low", "LLM_MAX_TOKENS": "4096"}


def reply(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                    finish_reason=finish_reason)])


def bad_request():
    request = httpx.Request("POST", "http://llm/chat/completions")
    return BadRequestError("unsupported parameter", response=httpx.Response(400, request=request), body=None)


class FakeCompletions:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def llm_with(*outcomes, env=ENV):
    completions = FakeCompletions(*outcomes)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return LLM(Settings.from_env(env), client=client), completions


def test_sends_optional_settings_when_configured():
    llm, calls = llm_with(reply('{"ok": true}'))
    assert llm.complete_json("sys", "user") == {"ok": True}
    assert calls.calls[0]["reasoning_effort"] == "low"
    assert calls.calls[0]["max_tokens"] == 4096
    assert calls.calls[0]["temperature"] == 0


def test_drops_optional_settings_after_a_400_and_remembers():
    llm, calls = llm_with(bad_request(), reply('{"a": 1}'), reply('{"b": 2}'))
    assert llm.complete_json("sys", "one") == {"a": 1}
    assert llm.complete_json("sys", "two") == {"b": 2}
    assert "reasoning_effort" not in calls.calls[1] and "temperature" not in calls.calls[1]
    assert "max_tokens" not in calls.calls[2]


def test_reasks_when_reply_is_not_json():
    llm, calls = llm_with(reply("Sure, here you go."), reply('{"fixed": true}'))
    assert llm.complete_json("sys", "user") == {"fixed": True}
    assert calls.calls[1]["messages"][-1]["content"].startswith("Reply with valid JSON only")


def test_empty_reply_from_spent_token_budget_is_an_error():
    llm, _ = llm_with(reply("", finish_reason="length"))
    with pytest.raises(LLMError, match="empty at the token limit"):
        llm.complete_json("sys", "user")


def test_truncated_reply_fails_at_once_instead_of_re_asking():
    llm, calls = llm_with(reply('{"units": [{"ids": ["s0001"], "te', finish_reason="length"))
    with pytest.raises(LLMError, match="cut off at the token limit"):
        llm.complete_json("sys", "user")
    assert len(calls.calls) == 1


def test_optional_settings_absent_when_not_configured():
    env = {k: v for k, v in ENV.items() if not k.startswith(("LLM_REASONING", "LLM_MAX"))}
    llm, calls = llm_with(reply("{}"), env=env)
    llm.complete_json("sys", "user")
    assert "reasoning_effort" not in calls.calls[0] and "max_tokens" not in calls.calls[0]
