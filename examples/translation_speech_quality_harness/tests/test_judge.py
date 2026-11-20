"""LLM judge and Claude adapter, with fake LLMs (no network)."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from bodhan_eval.cache import Cache
from bodhan_eval.checks.base import CheckResult, Flag
from bodhan_eval.clients import ClaudeLLMClient, LLMClient, LLMOutputError, make_llm_client
from bodhan_eval.clients.http import CacheMiss
from bodhan_eval.cost import CostMeter
from bodhan_eval.judge.judge import (
    SCHEMA,
    agreement,
    build_messages,
    consistency_sample,
    judge_item,
    judge_repeats,
    number_tokens,
    parse_judgement,
)
from bodhan_eval.languages import load_languages

L = load_languages()
SRC = "The bridge is 120 metres long. It opened in 1998."
TR = "पुल 120 मीटर लंबा है।\nयह 1999 में खुला।"


class FakeJudge:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def chat_json(self, messages, *, sample=0, max_tokens=2000, tag="judge", schema=None):
        self.calls.append({"messages": messages, "sample": sample, "schema": schema})
        r = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return r, False


def err(cat, a, b, sev="major", quote="", expl="wrong"):
    return {
        "category": cat,
        "severity": sev,
        "token_start": a,
        "token_end": b,
        "source_quote": quote,
        "explanation": expl,
    }


# --- prompt -----------------------------------------------------------------------------


def test_number_tokens_keeps_lines_and_offsets():
    toks, numbered = number_tokens(TR)
    assert numbered.splitlines()[0] == "[0]पुल [1]120 [2]मीटर [3]लंबा [4]है।"
    assert numbered.splitlines()[1].endswith("[8]खुला।")
    assert number_tokens("पानी 100 °C पर")[1] == "[0]पानी [1]100 °[2]C [3]पर"
    assert all(TR[t.start : t.end] == t.text for t in toks)


def test_messages_contain_rubric_tokens_bt_and_hints():
    _, numbered = number_tokens(TR)
    hint = Flag("number", "major", "numbers", 0.9, (20, 24), "source has 1998 but translation 1999")
    msgs = build_messages(
        SRC, numbered, L["hi"], "The bridge is 120 m long. It opened in 1999.", [hint]
    )
    assert msgs[0]["role"] == "system" and "token_start" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "[0]पुल" in user and "BACK-TRANSLATION" in user and "1998 but translation 1999" in user
    assert "Hindi" in user


# --- parsing ----------------------------------------------------------------------------


def test_parse_maps_token_range_to_exact_span():
    toks, _ = number_tokens(TR)
    i = next(k for k, t in enumerate(toks) if t.text == "1999")
    flags, info = parse_judgement(
        {
            "errors": [err("number", i, i, quote="1998", expl="1998 became 1999")],
            "verdict": "major",
        },
        SRC,
        TR,
        toks,
    )
    assert info["verdict"] == "major" and info["dropped"] == []
    f = flags[0]
    assert TR[f.span[0] : f.span[1]] == "1999"
    assert SRC[f.source_span[0] : f.source_span[1]] == "1998"
    assert f.check == "judge" and f.confidence == 0.6


def test_parse_drops_invalid_and_strips_indic_text():
    toks, _ = number_tokens(TR)
    raw = {
        "errors": [
            err("style", 0, 0),  # unknown category
            err("term", 0, 99),  # out of range
            err("mistranslation", 2, 1, quote="पुल", expl="गलत"),  # reversed range, Indic text
        ],
        "verdict": "maybe",
    }
    flags, info = parse_judgement(raw, SRC, TR, toks)
    assert len(flags) == 1 and len(info["dropped"]) == 2
    f = flags[0]
    assert f.details["token_start"] == 1 and f.details["token_end"] == 2
    assert "पुल" not in f.evidence and "गलत" not in f.evidence
    assert info["verdict"] is None


def test_parse_omission_at_start_uses_source_quote():
    toks, _ = number_tokens(TR)
    flags, _ = parse_judgement(
        {"errors": [err("omission", -1, -1, quote="It opened in 1998")], "verdict": "major"},
        SRC,
        TR,
        toks,
    )
    f = flags[0]
    assert SRC[f.source_span[0] : f.source_span[1]] == "It opened in 1998"
    assert f.span is not None and f.details["insertion_point"]


def test_parse_survives_garbage():
    flags, info = parse_judgement(["not", "a", "dict"], SRC, TR, [])
    assert flags == [] and info["dropped"]


def test_judge_item_end_to_end_with_fake():
    fake = FakeJudge([{"errors": [], "verdict": "clean"}])
    res = asyncio.run(judge_item(fake, SRC, TR, L["hi"], back_translation="..."))
    assert res.ran and res.flags == [] and res.signals["verdict"] == "clean"
    assert fake.calls[0]["schema"] is SCHEMA and fake.calls[0]["sample"] == 0


# --- consistency ------------------------------------------------------------------------


def test_consistency_sample_is_deterministic():
    ids = [f"item{i}" for i in range(50)]
    a = consistency_sample(ids, 5, seed=1)
    assert a == consistency_sample(list(reversed(ids)), 5, seed=1)
    assert len(a) == 5 and a != consistency_sample(ids, 5, seed=2)


def test_agreement_and_repeats():
    toks, _ = number_tokens(TR)
    same = {"errors": [err("number", 5, 5)], "verdict": "major"}
    diff = {"errors": [err("term", 0, 0)], "verdict": "minor"}
    fake = FakeJudge([same, same, diff])
    runs, ag = asyncio.run(judge_repeats(fake, SRC, TR, L["hi"], repeats=3))
    assert [c["sample"] for c in fake.calls] == [1, 2, 3]
    assert ag["runs"] == 3
    assert ag["verdict"] == pytest.approx(1 / 3, abs=1e-3)  # 1 of 3 pairs agree
    assert ag["flags"] == pytest.approx(1 / 3, abs=1e-3)
    clean = CheckResult("judge", signals={"verdict": "clean"})
    assert agreement([clean, clean]) == {"runs": 2, "verdict": 1.0, "flags": 1.0}


# --- Claude adapter ---------------------------------------------------------------------


class FakeMessages:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    async def create(self, **kw):
        self.calls.append(kw)
        return self.resp


def _resp(text, stop="end_turn"):
    return SimpleNamespace(
        id="msg_1",
        stop_reason=stop,
        stop_details=SimpleNamespace(category="cyber") if stop == "refusal" else None,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0),
    )


def _claude(tmp_path, resp, **kw):
    msgs = FakeMessages(resp)
    cost = CostMeter()
    c = ClaudeLLMClient(
        Cache(tmp_path / "c.sqlite"),
        cost,
        model="claude-haiku-5-5",
        client=SimpleNamespace(messages=msgs),
        **kw,
    )
    return c, msgs, cost


MSGS = [{"role": "system", "content": "rubric"}, {"role": "user", "content": "item"}]


def test_claude_request_shape_caching_and_cost(tmp_path):
    c, msgs, cost = _claude(tmp_path, _resp(json.dumps({"errors": [], "verdict": "clean"})))

    async def go():
        a = await c.chat_json(MSGS, schema=SCHEMA)
        b = await c.chat_json(MSGS, schema=SCHEMA)
        d = await c.chat_json(MSGS, schema=SCHEMA, sample=1)
        return a, b, d

    a, b, d = asyncio.run(go())
    assert a == ({"errors": [], "verdict": "clean"}, False)
    assert b[1] is True and d[1] is False  # same request cached; new sample is a new call
    kw = msgs.calls[0]
    assert kw["system"] == "rubric" and kw["messages"] == [MSGS[1]]
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert "temperature" not in kw and "effort" not in kw["output_config"]
    assert len(msgs.calls) == 2
    s = cost.summary()
    assert s["llm_tokens"] == {"input": 200, "output": 40} and s["services"]["llm"]["cached"] == 1


def test_claude_effort_passed_when_set(tmp_path):
    c, msgs, _ = _claude(tmp_path, _resp("{}"), effort="low")
    asyncio.run(c.chat_json(MSGS))
    assert msgs.calls[0]["output_config"] == {"effort": "low"}


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_claude_refusal_and_truncation_raise(tmp_path, stop):
    c, _, _ = _claude(tmp_path, _resp("", stop=stop))
    with pytest.raises(LLMOutputError):
        asyncio.run(c.chat_json(MSGS, schema=SCHEMA))


def test_claude_offline_cache_miss(tmp_path):
    c, msgs, _ = _claude(tmp_path, _resp("{}"), offline=True)
    with pytest.raises(CacheMiss):
        asyncio.run(c.chat_json(MSGS))
    assert msgs.calls == []


def test_make_llm_client_provider_selection(tmp_path, monkeypatch):
    cache, cost = Cache(tmp_path / "c.sqlite"), CostMeter()
    for k in ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert isinstance(make_llm_client(cache, cost), ClaudeLLMClient)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    assert isinstance(make_llm_client(cache, cost), LLMClient)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert isinstance(make_llm_client(cache, cost), ClaudeLLMClient)


def test_parse_strips_indic_from_explanation_but_keeps_english():
    toks, _ = number_tokens(TR)
    flags, _ = parse_judgement(
        {
            "errors": [err("mistranslation", 0, 0, expl="The word “পুল” should mean departure")],
            "verdict": "major",
        },
        SRC,
        TR,
        toks,
    )
    ev = flags[0].evidence
    assert "পুল" not in ev and "should mean departure" in ev and "…" in ev
