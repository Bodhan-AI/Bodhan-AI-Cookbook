"""The LLM wrapper switches to the fallback endpoint when the main one fails."""

import pytest
from openai import APIError, BadRequestError, RateLimitError

from bodhan_agent.llm import LLM, Endpoint, LLMError


class Unavailable(APIError):
    """Stands in for a 503 from the provider, without building a real HTTP response."""

    def __init__(self):
        Exception.__init__(self, "503 Service Unavailable")


class Limited(RateLimitError):
    """Stands in for a 429 from the provider."""

    def __init__(self):
        Exception.__init__(self, "429 Too Many Requests")
        self.response = None


class FakeCompletions:
    def __init__(self, fail_times=0, error=Unavailable):
        self.fail_times, self.error, self.calls = fail_times, error, 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error()
        message = type("M", (), {"content": "", "tool_calls": None})()
        return type("R", (), {"usage": None, "choices": [type("C", (), {"message": message})()]})()


def endpoint(name, completions):
    client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    return Endpoint(name, "https://example.test/v1", f"{name}-model", "key", client=client)


def test_falls_back_and_stays_on_the_fallback():
    main, backup = FakeCompletions(fail_times=99), FakeCompletions()
    llm = LLM([endpoint("main", main), endpoint("fallback", backup)])
    llm.chat([{"role": "user", "content": "hi"}], [])
    llm.chat([{"role": "user", "content": "hi"}], [])
    assert main.calls == 1 and backup.calls == 2  # the failing main is not tried again
    assert llm.model == "fallback-model" and llm.calls == 2
    assert llm.usage == {"main:main-model": {"calls": 0, "failures": 1}, "fallback:fallback-model": {"calls": 2, "failures": 0}}


def test_main_is_used_while_it_works():
    main, backup = FakeCompletions(), FakeCompletions()
    llm = LLM([endpoint("main", main), endpoint("fallback", backup)])
    llm.chat([{"role": "user", "content": "hi"}], [])
    assert main.calls == 1 and backup.calls == 0


def test_error_when_every_endpoint_fails():
    llm = LLM([endpoint("main", FakeCompletions(99)), endpoint("fallback", FakeCompletions(99))])
    with pytest.raises(LLMError, match="Every LLM endpoint failed"):
        llm.chat([{"role": "user", "content": "hi"}], [])


def test_fallback_needs_all_three_variables(monkeypatch):
    for name, value in {"LLM_BASE_URL": "https://a.test/v1", "LLM_MODEL": "a", "LLM_API_KEY": "k"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("LLM_FALLBACK_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "b")
    assert len(LLM.from_env().endpoints) == 1  # incomplete fallback is ignored
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "https://b.test/v1")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "k2")
    llm = LLM.from_env()
    assert [e.model for e in llm.endpoints] == ["a", "b"] and llm.endpoints[0].max_retries == 0
    assert llm.endpoints[0].client.max_retries == 0  # the client really uses it: no hidden retries


class FakeTime:
    def __init__(self):
        self.now, self.waits = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def test_single_endpoint_waits_out_a_short_rate_limit():
    main, t = FakeCompletions(fail_times=2, error=Limited), FakeTime()
    llm = LLM([endpoint("main", main)], sleep=t.sleep, clock=t.clock)
    llm.chat([{"role": "user", "content": "hi"}], [])
    assert main.calls == 3 and t.waits == [20.0, 20.0]


def test_rate_limited_main_is_used_again_once_its_limit_resets():
    main, backup, t = FakeCompletions(fail_times=1, error=Limited), FakeCompletions(), FakeTime()
    llm = LLM([endpoint("main", main), endpoint("fallback", backup)], sleep=t.sleep, clock=t.clock)
    llm.chat([{"role": "user", "content": "hi"}], [])  # main limited: the fallback answers at once
    assert backup.calls == 1 and t.waits == []
    t.now += 21
    llm.chat([{"role": "user", "content": "hi"}], [])  # limit reset: back on main
    assert main.calls == 2 and backup.calls == 1 and llm.model == "main-model"


def test_rejected_tool_history_is_resent_as_text():
    from bodhan_agent.llm import flatten_tool_history

    class Picky(FakeCompletions):
        def create(self, **kwargs):
            if any(m["role"] == "tool" for m in kwargs["messages"]):
                self.calls += 1
                raise type("Bad", (BadRequestError,), {"__init__": lambda self: Exception.__init__(self, "Error code: 400")})()
            return super().create(**kwargs)

    history = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
               {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "read_text", "arguments": "{}"}}]},
               {"role": "tool", "tool_call_id": "1", "content": "{\"ok\": true}"}]
    picky = Picky()
    llm = LLM([endpoint("main", picky)])
    llm.chat(history, [])
    assert picky.calls == 2  # rejected once, then accepted as text
    llm.chat(history, [])
    assert picky.calls == 3  # remembered: sent as text straight away, no second rejection
    flat = flatten_tool_history(history)
    assert [m["role"] for m in flat] == ["system", "user", "assistant", "user"] and "read_text" in flat[2]["content"]


def test_long_rate_limit_moves_to_the_fallback():
    main, backup = FakeCompletions(fail_times=99, error=Limited), FakeCompletions()
    waits = []
    llm = LLM([endpoint("main", main), endpoint("fallback", backup)], sleep=waits.append)
    llm.chat([{"role": "user", "content": "hi"}], [])
    assert sum(waits) <= 75 and backup.calls == 1 and llm.model == "fallback-model"


class Glitch(BadRequestError):
    """Stands in for Groq's 400 when the model calls a tool that does not exist."""

    def __init__(self):
        Exception.__init__(self, "Error code: 400 - Tool call validation failed: attempted to call tool 'commentary' which was not in request.tools")


class DailyLimit(RateLimitError):
    def __init__(self):
        Exception.__init__(self, "429 Too Many Requests")
        self.response = type("Resp", (), {"headers": {"retry-after": "3600"}})()


class Recording(FakeCompletions):
    def create(self, **kwargs):
        self.temperatures = getattr(self, "temperatures", []) + [kwargs.get("temperature")]
        return super().create(**kwargs)


def test_malformed_tool_call_is_asked_again_with_a_different_temperature():
    main, backup = Recording(fail_times=2, error=Glitch), FakeCompletions()
    llm = LLM([endpoint("main", main), endpoint("fallback", backup)])
    llm.chat([{"role": "user", "content": "hi"}], [])
    assert main.calls == 3 and backup.calls == 0 and main.temperatures == [0, 0.7, 0.7]


def test_repeated_malformed_tool_call_goes_to_the_fallback_without_resting():
    main, backup = FakeCompletions(fail_times=3, error=Glitch), FakeCompletions()
    llm = LLM([endpoint("main", main), endpoint("fallback", backup)])
    llm.chat([{"role": "user", "content": "hi"}], [])
    assert main.calls == 3 and backup.calls == 1
    llm.chat([{"role": "user", "content": "hi"}], [])
    assert main.calls == 4  # not rested: used again on the next call


def test_malformed_calls_with_the_fallback_resting_retry_main_then_stop():
    main = FakeCompletions(fail_times=99, error=Glitch)
    llm = LLM([endpoint("main", main), endpoint("fallback", FakeCompletions(99))])
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "hi"}], [])
    assert main.calls == 6  # two rounds of three tries


def test_daily_limit_switches_without_waiting():
    waits = []
    main, backup = FakeCompletions(fail_times=99, error=DailyLimit), FakeCompletions()
    llm = LLM([endpoint("main", main), endpoint("fallback", backup)], sleep=waits.append)
    llm.chat([{"role": "user", "content": "hi"}], [])
    assert waits == [] and main.calls == 1 and backup.calls == 1
