"""LLM client: header parsing, pacing from rate-limit headers, 429 handling, daily cap. No network."""

import pytest

from bodhan_dub import llm


class Resp:
    def __init__(self, status, payload=None, headers=None, text=""):
        self.status_code, self._p, self.headers, self.text = status, payload or {}, headers or {}, text

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._p


def ok_resp(content="YES", remaining="7000", reset="7.5s", completion=300):
    return Resp(200, {"choices": [{"message": {"content": content}}], "usage": {"completion_tokens": completion}},
                {"x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": remaining,
                 "x-ratelimit-reset-tokens": reset})


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    llm._PACERS.clear()
    llm._unavailable.clear()
    monkeypatch.setattr(llm, "get_llm_config", lambda: {"base_url": "http://x", "model": "m", "key": "k"})
    sleeps = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def test_parse_duration_formats_seen_in_headers():
    assert llm.parse_duration("585ms") == pytest.approx(0.585)
    assert llm.parse_duration("7.66s") == pytest.approx(7.66)
    assert llm.parse_duration("1m2.5s") == pytest.approx(62.5)
    assert llm.parse_duration("5h25m26.4s") == pytest.approx(19526.4)
    assert llm.parse_duration("2") == 2.0
    assert llm.parse_duration("soon") is None and llm.parse_duration(None) is None


def test_pacer_learns_refill_rate_and_waits_only_as_long_as_needed():
    t = {"now": 0.0}
    slept = []
    p = llm.Pacer(clock=lambda: t["now"], sleep=lambda s: (slept.append(s), t.__setitem__("now", t["now"] + s)))
    p.update({"x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "200",
              "x-ratelimit-reset-tokens": "58.5s"})           # 7800 tokens refill in 58.5 s -> 133.3 tokens/s
    assert p.rate == pytest.approx(7800 / 58.5)
    waited = p.acquire(1000)                                  # needs 800 more tokens: about 6 s, not 58.5 s
    assert 5.5 < waited < 7.0
    assert p.acquire(10) > 0                                  # the reservation was taken: bucket nearly empty


def test_no_headers_means_no_pacing():
    p = llm.Pacer(sleep=lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")))
    assert p.acquire(5000) == 0.0
    p.update({})
    assert p.acquire(5000) == 0.0


def test_pacers_are_per_model():
    assert llm.pacer_for("a") is llm.pacer_for("a") and llm.pacer_for("a") is not llm.pacer_for("b")


def test_429_waits_for_the_reset_the_server_gives_then_succeeds(monkeypatch, fresh):
    calls = iter([Resp(429, headers={"retry-after": "3"}, text="Rate limit reached ... tokens per minute (TPM)"),
                  ok_resp("fine")])
    monkeypatch.setattr(llm, "post_with_retry", lambda *a, **k: next(calls))
    assert llm.chat([{"role": "user", "content": "hi"}]) == "fine"
    assert fresh and fresh[0] == pytest.approx(3.25)


def test_daily_cap_fails_fast_and_stays_off(monkeypatch):
    n = {"c": 0}

    def post(*a, **k):
        n["c"] += 1
        return Resp(429, headers={"x-ratelimit-remaining-requests": "0"}, text="limit on requests per day (RPD)")

    monkeypatch.setattr(llm, "post_with_retry", post)
    with pytest.raises(llm.LLMUnavailable):
        llm.chat([{"role": "user", "content": "hi"}])
    with pytest.raises(llm.LLMUnavailable):
        llm.chat([{"role": "user", "content": "hi"}])
    assert n["c"] == 1


def test_gives_up_with_an_error_not_an_answer(monkeypatch):
    monkeypatch.setattr(llm, "post_with_retry", lambda *a, **k: Resp(429, text="tokens per minute"))
    with pytest.raises(RuntimeError, match="no answer"):
        llm.chat([{"role": "user", "content": "hi"}])
