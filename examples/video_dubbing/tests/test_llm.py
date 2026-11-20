"""The LLM is optional: when it is missing or down, calls fail at once and callers use their fallbacks."""

import time
from types import SimpleNamespace

import httpx2
import openai
import pytest
from bodhan_dub import llm as llm_module
from bodhan_dub.llm import LLM, LLMError, LLMTruncated
from bodhan_dub.speech import shorten_all

REQUEST = httpx2.Request("POST", "https://llm.example/v1/chat/completions")


def status_error(code: int) -> openai.APIStatusError:
    return openai.APIStatusError(f"HTTP {code}", response=httpx2.Response(code, request=REQUEST), body=None)


class FakeCompletions:
    """Raises each queued error in turn, then replies with `reply`."""

    def __init__(self, errors=(), reply='{"text": "ok"}', finish="stop"):
        self.errors, self.reply, self.finish, self.calls = list(errors), reply, finish, 0

    def create(self, **_):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        message = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=self.finish)])


@pytest.fixture
def make_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_module.time, "sleep", lambda _: None)

    def make(**fake):
        llm = LLM(tmp_path)
        llm.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(**fake)))
        return llm, llm.client.chat.completions
    return make


def test_missing_settings_turn_the_llm_off_without_crashing(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm = LLM(tmp_path)
    assert llm.down and not llm.summary()["available"]
    with pytest.raises(LLMError):
        llm.ask_json("anything")


def test_a_bad_key_stops_all_later_calls_without_retrying(make_llm):
    llm, fake = make_llm(errors=[status_error(401)])
    with pytest.raises(LLMError):
        llm.ask_json("first")
    with pytest.raises(LLMError):
        llm.ask_json("second")
    assert fake.calls == 1 and "401" in llm.down


def test_transient_errors_are_retried(make_llm):
    llm, fake = make_llm(errors=[status_error(503), openai.APITimeoutError(REQUEST)])
    assert llm.ask_json("p") == {"text": "ok"}
    assert fake.calls == 3 and not llm.down


def test_retries_exhausted_once_then_every_call_fails_fast(make_llm):
    llm, fake = make_llm(errors=[openai.APITimeoutError(REQUEST)] * llm_module.TRIES)
    with pytest.raises(LLMError):
        llm.ask_json("first")
    assert fake.calls == llm_module.TRIES and llm.down
    started = time.monotonic()
    with pytest.raises(LLMError):
        llm.ask_json("second")
    assert fake.calls == llm_module.TRIES and time.monotonic() - started < 0.1


def test_a_rejected_prompt_does_not_turn_the_llm_off(make_llm):
    llm, fake = make_llm(errors=[status_error(400)])
    with pytest.raises(LLMError):
        llm.ask_json("too long")
    assert not llm.down
    assert llm.ask_json("fine") == {"text": "ok"} and fake.calls == 2


def test_a_reply_cut_off_at_the_output_limit_is_its_own_error(make_llm):
    llm, fake = make_llm(reply='<reasoning>still thinking', finish="length")
    with pytest.raises(LLMTruncated):
        llm.ask_json("long")
    assert fake.calls == 1 and not llm.down


def test_cached_replies_are_served_while_down(make_llm):
    llm, _ = make_llm()
    assert llm.ask_json("p") == {"text": "ok"}
    llm.down = "gone"
    assert llm.ask_json("p") == {"text": "ok"}


def test_shorten_falls_back_when_the_llm_is_down(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert shorten_all([("A long sentence about 3 angles.", 4, 1)], LLM(tmp_path)) == [None]


@pytest.mark.parametrize("reply", [
    '<reasoning>They want {"text": "..."} so I answer.</reasoning>{"text": "ok"}',
    '<think>Maybe {"text": "draft"}?</think>\n```json\n{"text": "ok"}\n```',
    'Here you go: {"text": "ok"}',
    '{"text": "ok", "nested": {"a": 1}}',
])
def test_json_is_found_after_reasoning_and_fences(reply):
    assert llm_module._parse_json(reply)["text"] == "ok"


def test_a_reply_without_json_is_an_error():
    with pytest.raises(LLMError):
        llm_module._parse_json("<reasoning>{not json}</reasoning> sorry")
