"""HTTP client behaviour: retries, error mapping, caching and the rupee budget."""

import httpx
import pytest
import respx
from conftest import BASE, make_ctx, to_wav, tone

from bodhan_mcp.errors import ToolError


async def test_identical_request_is_sent_once(workdir, env_keys, fake_api):
    ctx = make_ctx(workdir)
    a = await ctx.client.translate("Water boils at 100 degrees.", "hi")
    b = await ctx.client.translate("Water boils at 100 degrees.", "hi")
    assert a == b
    assert fake_api.calls["translate"] == 1
    summary = ctx.ledger.summary()["by_model"]["indic-translate"]
    assert summary["calls"] == 2 and summary["cache_hits"] == 1


async def test_rate_limit_is_retried_with_backoff(workdir, env_keys, fake_api):
    ctx = make_ctx(workdir)
    fake_api.rate_limit_once = True
    text = await ctx.client.transcribe(to_wav(tone(1.0)), 1.0, "hi")
    assert text
    assert fake_api.calls["transcribe"] == 2


async def test_budget_refuses_before_spending(workdir, env_keys, fake_api):
    ctx = make_ctx(workdir, max_cost=0.001)
    with pytest.raises(ToolError) as exc:
        await ctx.client.speak("x" * 500, "Kavya", "hi")  # about ₹0.30
    assert exc.value.code == "budget_exceeded"
    assert fake_api.calls["speak"] == 0
    assert ctx.ledger.spent == 0


async def test_costs_are_booked_per_model(workdir, env_keys, fake_api):
    ctx = make_ctx(workdir)
    await ctx.client.speak("नमस्ते दुनिया", "Kavya", "hi")
    await ctx.client.ocr(b"\x89PNG fake")
    s = ctx.ledger.summary()
    assert s["by_model"]["indic-speak"]["cost_inr"] == pytest.approx(13 / 10000 * 6, rel=1e-3)
    assert s["by_model"]["indic-ocr"]["cost_inr"] == pytest.approx(0.20)


@pytest.mark.parametrize("status,code", [(413, "input_too_long"), (415, "unsupported_format"), (401, "missing_api_key"), (422, "invalid_argument")])
async def test_http_errors_map_to_codes(workdir, env_keys, status, code):
    ctx = make_ctx(workdir)
    with respx.mock(base_url=BASE) as router:
        router.post("/translate").mock(return_value=httpx.Response(status, json={"error": {"code": "x", "message": "nope", "request_id": "r1"}}))
        with pytest.raises(ToolError) as exc:
            await ctx.client.translate("hello", "ta")
    assert exc.value.code == code
    assert exc.value.details["request_id"] == "r1"


async def test_missing_key_is_actionable(workdir, monkeypatch, fake_api):
    for k in ("BODHAN_TRANSLATE_KEY", "BODHAN_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    ctx = make_ctx(workdir)
    with pytest.raises(ToolError) as exc:
        await ctx.client.translate("hello", "ta")
    assert exc.value.code == "missing_api_key"
    assert "BODHAN_TRANSLATE_KEY" in exc.value.hint
