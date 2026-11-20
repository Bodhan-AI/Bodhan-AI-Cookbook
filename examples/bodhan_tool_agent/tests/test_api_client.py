import httpx
import pytest

from bodhan_mcp import costs
from bodhan_mcp.bodhan_api import BodhanAPI
from bodhan_mcp.errors import ToolFailure


async def test_retries_on_429_and_5xx_then_succeeds(rt, fake):
    fake.fail_next = [429, 503]
    meter = costs.CallMeter()
    r = await rt.api.translate("hello", "hi", meter=meter)
    assert r["translation"]
    assert meter.retries == 2 and meter.billed_calls == 1


async def test_gives_up_with_structured_error(rt, fake):
    fake.fail_next = [500] * 10
    with pytest.raises(ToolFailure) as exc:
        await rt.api.translate("hello", "hi")
    assert exc.value.code == "upstream_error" and exc.value.retryable


async def test_cache_means_no_second_request(rt, fake):
    m1, m2 = costs.CallMeter(), costs.CallMeter()
    await rt.api.translate("same input", "ta", meter=m1)
    await rt.api.translate("same input", "ta", meter=m2)
    assert fake.calls["translate"] == 1
    assert m1.cost_inr > 0 and m2.cost_inr == 0 and m2.cache_hits == 1
    assert not fake.duplicate_requests()


async def test_inflight_requests_are_deduplicated(rt, fake):
    import asyncio

    await asyncio.gather(*(rt.api.translate("parallel input", "bn") for _ in range(5)))
    assert fake.calls["translate"] == 1


async def test_missing_key_is_actionable(settings, fake):
    settings.keys["indic-speak"] = None
    api = BodhanAPI(settings, transport=httpx.MockTransport(fake.handler))
    with pytest.raises(ToolFailure) as exc:
        await api.speak("नमस्ते", "Kavya", "hi")
    assert exc.value.code == "missing_api_key"
    assert exc.value.details["env_var"] == "BODHAN_SPEAK_KEY"


async def test_unsupported_language_maps_to_code(rt):
    with pytest.raises(ToolFailure) as exc:
        await rt.api.translate("hello", "fr")
    assert exc.value.code == "unsupported_language"


async def test_cost_accounting_matches_price_list(rt):
    meter = costs.CallMeter()
    await rt.api.speak("a" * 100, "Kavya", "hi", meter=meter)
    assert meter.cost_inr == pytest.approx(100 * 6 / 10000)
    snap = rt.api.ledger.snapshot()
    assert snap["models"]["indic-speak"]["input_chars"] == 100
