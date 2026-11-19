from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fakes import fake_runtime

from bodhan_mcp.client import track_usage
from bodhan_mcp.errors import ToolFailure
from bodhan_mcp.runtime import Runtime

pytestmark = pytest.mark.asyncio


async def test_cache_prevents_duplicate_calls_across_runs(tmp_path: Path) -> None:
    rt, fake = fake_runtime(tmp_path)
    first = await rt.client.translate("The meeting is postponed.", "hi")
    again = await rt.client.translate("The meeting is postponed.", "hi")
    assert first == again and fake.calls["/v1/chat/completions"] == 1

    # A fresh process with the same cache directory still does not pay again.
    rt2 = Runtime(rt.settings, transport=rt.client.transport)
    with track_usage() as usage:
        await rt2.client.translate("The meeting is postponed.", "hi")
    assert fake.calls["/v1/chat/completions"] == 1 and usage.cache_hits == 1 and usage.cost_inr == 0
    assert rt2.client.meter.records[-1]["cache_hit"] is True


async def test_concurrent_identical_requests_share_one_call(tmp_path: Path) -> None:
    rt, fake = fake_runtime(tmp_path)
    results = await asyncio.gather(*(rt.client.speak("नमस्ते", voice="Kavya", lang="hi") for _ in range(5)))
    assert len(set(results)) == 1 and fake.calls["/v1/audio/speech"] == 1


async def test_budget_exceeded_before_any_paid_call(tmp_path: Path) -> None:
    rt, fake = fake_runtime(tmp_path, settings={"max_cost_inr": 0.01})
    text = "x" * 500  # INR 0.30 of speech
    with pytest.raises(ToolFailure) as err:
        await rt.client.speak(text, voice="Kavya", lang="hi")
    assert err.value.code == "budget_exceeded"
    assert fake.calls["/v1/audio/speech"] == 0 and rt.client.meter.spent_inr == 0


async def test_retries_429_then_succeeds(tmp_path: Path) -> None:
    rt, fake = fake_runtime(tmp_path, fail_plan={"/v1/chat/completions": [429, 503]})
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    rt.client.sleep = record_sleep
    body = await rt.client.translate("hello", "ta")
    assert body["translation"] and fake.calls["/v1/chat/completions"] == 3
    assert sleeps[0] == 0.0  # Retry-After: 0 is honoured
    assert len(sleeps) == 2 and sleeps[1] > 0  # then exponential backoff with jitter


async def test_gives_up_with_rate_limited(tmp_path: Path) -> None:
    rt, fake = fake_runtime(tmp_path, settings={"max_retries": 2}, fail_plan={"/v1/chat/completions": [429] * 5})
    with pytest.raises(ToolFailure) as err:
        await rt.client.translate("hello", "ta")
    assert err.value.code == "rate_limited" and err.value.retryable
    assert fake.calls["/v1/chat/completions"] == 3


async def test_error_mapping(tmp_path: Path) -> None:
    rt, _ = fake_runtime(tmp_path)
    with pytest.raises(ToolFailure) as err:
        await rt.client.translate("hello", "bho")
    assert err.value.code == "unsupported_language"

    with pytest.raises(ToolFailure) as err:
        await rt.client.speak("   ", voice="Kavya", lang="hi")
    assert err.value.code == "invalid_argument"

    rt, _ = fake_runtime(tmp_path / "b", settings={"keys": {}})
    with pytest.raises(ToolFailure) as err:
        await rt.client.translate("hello", "ta")
    assert err.value.code == "missing_api_key" and "BODHAN_TRANSLATE_KEY" in err.value.hint


async def test_translation_uses_chat_route_and_reports_truncation(tmp_path: Path) -> None:
    rt, fake = fake_runtime(tmp_path, truncate_over=10)
    body = await rt.client.translate("a fairly long sentence here", "hi")
    assert body["truncated"] is True and body["translation"]
    assert fake.calls["/v1/chat/completions"] == 1 and fake.calls["/translate"] == 0


async def test_meter_records_paid_calls(tmp_path: Path) -> None:
    rt, _ = fake_runtime(tmp_path)
    await rt.client.speak("abcdefghij", voice="Kavya", lang="hi")
    rec = rt.client.meter.records[-1]
    assert rec["model"] == "indic-speak" and rec["cache_hit"] is False and rec["units"] == 10
    assert abs(rt.client.meter.spent_inr - 10 / 10_000 * 6) < 1e-9
