"""Bodhan client: retries, billing classification, cache, budget reservations."""

import asyncio
import json

import pytest

from bodhan_mcp import errors as E
from bodhan_mcp.runtime import CostLedger


async def test_rate_limit_is_retried_then_succeeds(fake, make_ctx):
    fake.fail_first["translate"] = [429, 429]
    events: list = []
    ctx = make_ctx(fake, events=events)
    meta, info = await ctx.client.translate("Hello there", "hi")
    assert info["attempts"] == 3 and meta["translation"]
    assert [e.get("error") for e in events if e["type"] == "api_call"][:2] == ["rate_limited", "rate_limited"]
    assert ctx.ledger.uncertain == 0  # 429s are not billed


async def test_timeout_counts_as_uncertain_cost_and_retries(fake, make_ctx):
    fake.fail_first["translate"] = [0]  # simulated read timeout
    ctx = make_ctx(fake)
    await ctx.client.translate("Hello", "ta")
    assert ctx.ledger.uncertain > 0, "a timed-out request may have been billed and must not be treated as free"


async def test_credit_exhausted_is_not_retried(fake, make_ctx):
    fake.fail_first["translate"] = [4291]
    ctx = make_ctx(fake)
    with pytest.raises(E.ToolError) as exc:
        await ctx.client.translate("Hello", "ta")
    assert exc.value.code == E.CREDIT_EXHAUSTED and fake.calls["translate"] == 1


async def test_server_errors_exhaust_retries_with_structured_error(fake, make_ctx):
    fake.fail_first["speech"] = [503, 503, 503]
    ctx = make_ctx(fake)
    with pytest.raises(E.ToolError) as exc:
        await ctx.client.speak("नमस्ते", "Kavya", "hi")
    assert exc.value.code == E.UPSTREAM_UNAVAILABLE and exc.value.retryable
    assert fake.calls["speech"] == 3


async def test_cache_prevents_sending_same_input_twice(fake, make_ctx):
    ctx = make_ctx(fake)
    await ctx.client.translate("Same text", "bn")
    await ctx.client.translate("Same text", "bn")
    assert fake.calls["translate"] == 1 and ctx.ledger.cache_hits == 1
    await ctx.client.translate("Same text", "ta")  # different settings -> new request
    assert fake.calls["translate"] == 2


async def test_request_contracts(fake, make_ctx):
    ctx = make_ctx(fake)
    await ctx.client.translate("Hi", "kn")
    assert fake.requests[-1] == {"kind": "translate", "target": "kn", "text": "Hi"}
    await ctx.client.speak("ನಮಸ್ಕಾರ", "Deepika", "kn", "news")
    assert fake.requests[-1]["lang"] == "kn"


async def test_unsupported_language_from_api_is_mapped(fake, make_ctx):
    ctx = make_ctx(fake)
    with pytest.raises(E.ToolError) as exc:
        await ctx.client.translate("Hello", "fr")
    assert exc.value.code == E.UNSUPPORTED_LANGUAGE and not exc.value.retryable


def test_ledger_reservations_never_exceed_budget():
    ledger = CostLedger(max_inr=1.0)
    held = []
    with pytest.raises(E.ToolError) as exc:
        for _ in range(20):
            held.append(ledger.reserve("indic-speak", 0.3, "speak"))
    assert len(held) == 3 and exc.value.code == E.BUDGET_EXCEEDED
    assert exc.value.details["remaining_inr"] == pytest.approx(0.1)
    ledger.commit(held[0], 0.1)
    ledger.release(held[1])
    ledger.mark_uncertain(held[2])
    assert ledger.reserved == pytest.approx(0) and ledger.committed == pytest.approx(0.4)


async def test_concurrent_calls_cannot_overspend(fake, make_ctx):
    ctx = make_ctx(fake, max_inr=0.02)  # each request is ~22 chars = ₹0.0132: room for exactly one

    async def one(i):
        try:
            await ctx.client.speak("नमस्ते " * 3 + str(i), "Kavya", "hi")
            return True
        except E.ToolError as exc:
            assert exc.code == E.BUDGET_EXCEEDED
            return False

    results = await asyncio.gather(*[one(i) for i in range(10)])
    assert ctx.ledger.committed <= 0.02 + 1e-9
    assert results.count(True) == 1 and results.count(False) == 9


async def test_missing_key_is_actionable(fake, tmp_path):
    from bodhan_mcp.client import BodhanClient
    from bodhan_mcp.config import BodhanKeys
    from bodhan_mcp.runtime import ResultCache

    client = BodhanClient(BodhanKeys(None, None, None, None), CostLedger(), ResultCache(None), transport=fake.transport)
    with pytest.raises(E.ToolError) as exc:
        await client.ocr(b"\x89PNG", "image/png")
    assert exc.value.code == E.MISSING_CREDENTIALS and "BODHAN_OCR_KEY" in exc.value.message
    assert json.dumps(exc.value.as_dict())


async def test_rate_limit_burst_backs_off_for_the_whole_model_and_recovers(fake, make_ctx):
    fake.fail_first["transcriptions"] = [429] * 5  # a busy server: several requests rejected in a row
    ctx = make_ctx(fake)
    from fakes import wav_tone

    clips = [wav_tone(2.0 + i * 0.1, rate=16000) for i in range(6)]
    results = await asyncio.gather(*[ctx.client.transcribe(c, 2.0, "hi") for c in clips])
    assert len(results) == 6  # every request eventually succeeds; none is reported as failed
    gate = ctx.client.gates["indic-transcribe"]
    assert gate.limit < gate.max  # halved to 1 during the burst; six successes have only grown it back part-way
    assert ctx.ledger.uncertain == 0  # 429s are never billed


async def test_rate_limit_gives_up_after_bounded_attempts(fake, make_ctx):
    fake.fail_first["translate"] = [429] * 20
    ctx = make_ctx(fake)
    with pytest.raises(E.ToolError) as exc:
        await ctx.client.translate("Hello", "hi")
    assert exc.value.code == E.RATE_LIMITED and exc.value.details["attempts"] == ctx.client.max_rate_limit_attempts
