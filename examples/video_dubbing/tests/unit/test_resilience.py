"""
Unit tests for resilience layer:
- Isolated circuit breakers per (service, language)
- Semaphore release during 429 backoff
"""

import asyncio
import time

import httpx
import pytest
from fake_bodhan.server import FakeBodhanTransport

from bodhan_dub.config import Settings
from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.gateway.cache import DiskCache
from bodhan_dub.gateway.cost import BudgetGuard, CostLedger
from bodhan_dub.gateway.resilience import (
    CircuitBreakerOpen,
    execute_with_resilience,
)
from bodhan_dub.logging import PipelineLogger


@pytest.mark.asyncio
async def test_circuit_breaker_isolation_tamil_speak_tripped_hindi_transcribe_works(tmp_path):
    """
    C2: Assert trip Tamil speak (5x 500 errors) causes Tamil speak circuit breaker to OPEN,
    while Hindi transcribe and Telugu speak still work cleanly.
    """
    transport = FakeBodhanTransport(failure_injection={"speak_500_langs": ["ta"]})
    settings = Settings(circuit_breaker_threshold=5)
    cache = DiskCache(cache_dir=tmp_path / "cache", enabled=False)
    ledger = CostLedger()
    guard = BudgetGuard(ledger)
    logger = PipelineLogger(run_id="test_cb", log_file=tmp_path / "test.log")

    client = BodhanClient(
        settings=settings,
        cache=cache,
        ledger=ledger,
        budget_guard=guard,
        logger=logger,
        transport=transport,
    )

    # 1. Trigger 5 failures on Tamil speak to trip breaker
    for i in range(5):
        try:
            await client.speak(
                text="வணக்கம்",
                voice="Kavya",
                language_code="ta",
                stage="tts",
                lang="ta",
                segment_id=f"seg_{i}",
            )
        except Exception:
            pass

    # Assert Tamil speak breaker is now OPEN
    ta_breaker = client.cb_registry.get_breaker("speak", "ta")
    assert ta_breaker.state == "OPEN"

    # Attempting Tamil speak now must raise CircuitBreakerOpen immediately without network call
    with pytest.raises(CircuitBreakerOpen):
        await client.speak(
            text="வணக்கம்",
            voice="Kavya",
            language_code="ta",
            stage="tts",
            lang="ta",
            segment_id="seg_failfast",
        )

    # 2. Hindi transcribe must still work cleanly (separate service and lang)
    transcribe_res = await client.transcribe(
        audio_bytes=b"fake-wav-bytes",
        language="hi",
        stage="asr",
    )
    assert transcribe_res.get("text") != ""
    hi_asr_breaker = client.cb_registry.get_breaker("transcribe", "hi")
    assert hi_asr_breaker.state == "CLOSED"

    # 3. Hindi speak must also still work cleanly
    hi_wav = await client.speak(
        text="नमस्ते",
        voice="Kavya",
        language_code="hi",
        stage="tts",
        lang="hi",
        segment_id="hi_seg",
    )
    assert len(hi_wav) > 0

    await client.close()


@pytest.mark.asyncio
async def test_semaphore_not_held_during_429_backoff():
    """
    C2: Assert semaphore is released during 429 backoff sleep so other concurrent tasks proceed.
    """
    sem = asyncio.Semaphore(1)
    call_timeline = []

    async def mock_rate_limited_call():
        attempt = 0

        async def _call():
            nonlocal attempt
            attempt += 1
            call_timeline.append(("req1", time.time()))
            if attempt == 1:
                # First attempt returns 429 with 0.15s retry-after
                return httpx.Response(429, headers={"Retry-After": "0.15"}, json={})
            return httpx.Response(200, json={"status": "ok"})

        return await execute_with_resilience(
            coro_fn=_call,
            semaphore=sem,
            max_attempts=3,
        )

    async def mock_concurrent_call():
        # Small delay to ensure req1 enters first and gets 429
        await asyncio.sleep(0.02)
        async def _call():
            call_timeline.append(("req2", time.time()))
            return httpx.Response(200, json={"status": "ok"})

        return await execute_with_resilience(
            coro_fn=_call,
            semaphore=sem,
            max_attempts=3,
        )

    # Launch both concurrently with semaphore=1
    res1, res2 = await asyncio.gather(mock_rate_limited_call(), mock_concurrent_call())
    assert res1.status_code == 200
    assert res2.status_code == 200

    # Verify that req2 was able to execute while req1 was sleeping in backoff
    tags = [tag for tag, _ in call_timeline]
    # Expected order: req1 (429), req2 (200 during req1 sleep), req1 retry (200)
    assert tags == ["req1", "req2", "req1"]
