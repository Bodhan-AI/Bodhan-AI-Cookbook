"""
Unit regression tests for Batch C: Gateway, budget, and resilience.
Covers:
- C1: Circuit breaker trips after exactly N consecutive 5xx (not N/2), no sleep after final attempt.
- C2: Atomic budget reservation prevents overshoot under concurrent calls.
- C3: Single-flight in-flight deduplication collapses concurrent identical requests to 1 paid network call.
- C4: Fast-fail on unknown voice/language 400/404/422 trips breaker and skips remaining segments for that language.
- C5: Preflight rejects invalid --voices specification with exit code 2.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from bodhan_dub.cli import async_main
from bodhan_dub.config import Settings
from bodhan_dub.gateway.bodhan import BodhanClient, UnknownVoiceOrLanguageError
from bodhan_dub.gateway.cache import DiskCache
from bodhan_dub.gateway.cost import BudgetExceededError, BudgetGuard, CostLedger
from bodhan_dub.gateway.resilience import (
    CircuitBreaker,
    CircuitBreakerOpen,
    execute_with_resilience,
)
from bodhan_dub.languages import LanguageRegistry
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.preflight import PreflightError, validate_voices_mapping


def make_mock_settings(tmp_path: Path):
    s = MagicMock(spec=Settings)
    s.cache_dir = tmp_path / "cache"
    s.bodhan_base_url = "https://api.bodhan.ai/v1"
    s.bodhan_transcribe_key = "test_key"
    s.bodhan_translate_key = "test_key"
    s.bodhan_speak_key = "test_key"
    s.llm_base_url = "https://api.groq.com/openai/v1"
    s.llm_api_key = "test_key"
    s.circuit_breaker_threshold = 5
    s.timeout_asr = 5.0
    s.timeout_translate = 5.0
    s.timeout_tts = 5.0
    s.semaphore_asr = 4
    s.semaphore_translate = 4
    s.semaphore_tts = 4
    return s


# ---------------------------------------------------------------------------
# C1: Breaker trips after exactly N consecutive 5xx (not N/2)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_C1_resilience_circuit_breaker_exact_count():
    threshold = 5
    cb = CircuitBreaker(failure_threshold=threshold, recovery_timeout=60.0)
    sem = asyncio.Semaphore(5)

    fail_count = 0

    async def _failing_call():
        nonlocal fail_count
        fail_count += 1
        resp = httpx.Response(500, request=httpx.Request("POST", "https://api.bodhan.ai"))
        return resp

    # Run separate failed calls with max_attempts=1 to isolate per-call recording
    for i in range(threshold - 1):
        with pytest.raises(httpx.HTTPStatusError):
            await execute_with_resilience(
                _failing_call,
                semaphore=sem,
                circuit_breaker=cb,
                max_attempts=1,
            )
        assert cb.consecutive_failures == i + 1
        assert cb.state == "CLOSED"

    # The 5th failure must trip the breaker to OPEN
    with pytest.raises(httpx.HTTPStatusError):
        await execute_with_resilience(
            _failing_call,
            semaphore=sem,
            circuit_breaker=cb,
            max_attempts=1,
        )

    assert cb.consecutive_failures == threshold
    assert cb.state == "OPEN"

    # Subsequent call immediately fails with CircuitBreakerOpen without invoking coro_fn
    with pytest.raises(CircuitBreakerOpen):
        await execute_with_resilience(
            _failing_call,
            semaphore=sem,
            circuit_breaker=cb,
            max_attempts=1,
        )


# ---------------------------------------------------------------------------
# C2: Atomic budget reservation prevents overshoot under concurrency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_C2_budget_reservation_concurrency(tmp_path: Path):
    ledger = CostLedger()
    # Cost per speak of 10,000 chars is ₹6.00 -> 1000 chars = ₹0.60
    # Set max budget to ₹1.50 -> can support at most 2 calls of ₹0.60 (total ₹1.20)
    # A 3rd concurrent call of ₹0.60 would take it to ₹1.80 > ₹1.50 and must fail!
    guard = BudgetGuard(ledger=ledger, max_cost_inr=1.50)

    success_count = 0
    failure_count = 0

    async def _concurrent_worker(worker_id: int):
        nonlocal success_count, failure_count
        cost_estimate = 0.60
        try:
            async with guard.reserve(cost_estimate):
                # Simulate in-flight processing delay
                await asyncio.sleep(0.05)
                # Reconcile actual spend
                await ledger.record_speak(1000, from_cache=False)
                success_count += 1
        except BudgetExceededError:
            failure_count += 1

    # Launch 6 concurrent workers simultaneously
    tasks = [_concurrent_worker(i) for i in range(6)]
    await asyncio.gather(*tasks)

    # Exactly 2 calls should succeed (2 * 0.60 = 1.20 <= 1.50)
    # The other 4 calls must be cleanly rejected
    assert success_count == 2
    assert failure_count == 4
    total_spent = await ledger.get_total_inr()
    assert total_spent <= 1.50


# ---------------------------------------------------------------------------
# C3: Single-flight in-flight deduplication
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_C3_single_flight_dedupe(tmp_path: Path):
    settings = make_mock_settings(tmp_path)
    cache = DiskCache(cache_dir=tmp_path / "cache", enabled=True)
    ledger = CostLedger()
    guard = BudgetGuard(ledger=ledger)
    logger = PipelineLogger(run_id="test_c3", log_file=tmp_path / "log.jsonl")

    network_call_count = 0

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_call_count
        network_call_count += 1
        # Add slight delay to keep request in-flight
        import time
        time.sleep(0.05)
        response_json = {
            "choices": [{"message": {"content": "नमस्ते दुनिया"}}],
            "usage": {"completion_tokens": 12},
        }
        return httpx.Response(200, json=response_json, request=request)

    transport = httpx.MockTransport(mock_transport_handler)
    client = BodhanClient(
        settings=settings,
        cache=cache,
        ledger=ledger,
        budget_guard=guard,
        logger=logger,
        transport=transport,
    )

    # Launch 5 concurrent identical translates simultaneously
    tasks = [
        client.translate(
            text="Hello world",
            target_language_code="hi",
            source_language_code="en",
        )
        for _ in range(5)
    ]

    results = await asyncio.gather(*tasks)

    # All 5 calls must receive identical translation content
    for text, usage in results:
        assert text == "नमस्ते दुनिया"
        assert usage.get("completion_tokens") == 12

    # Exactly 1 paid network call must have been dispatched
    assert network_call_count == 1
    await client.close()


# ---------------------------------------------------------------------------
# C4: Fast-fail on unknown voice/language 400/404/422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_C4_fast_fail_unavailable_voice(tmp_path: Path):
    settings = make_mock_settings(tmp_path)
    cache = DiskCache(cache_dir=tmp_path / "cache", enabled=False)
    ledger = CostLedger()
    guard = BudgetGuard(ledger=ledger)
    logger = PipelineLogger(run_id="test_c4", log_file=tmp_path / "log.jsonl")

    tts_call_count = 0

    def mock_tts_handler(request: httpx.Request) -> httpx.Response:
        nonlocal tts_call_count
        tts_call_count += 1
        return httpx.Response(
            400,
            json={"error": "Unsupported voice 'InvalidVoice' for language 'hi'"},
            request=request,
        )

    transport = httpx.MockTransport(mock_tts_handler)
    client = BodhanClient(
        settings=settings,
        cache=cache,
        ledger=ledger,
        budget_guard=guard,
        logger=logger,
        transport=transport,
    )

    # Attempting to synthesize with an invalid voice must raise UnknownVoiceOrLanguageError
    # and trip the language breaker
    with pytest.raises(UnknownVoiceOrLanguageError) as exc_info:
        await client.speak(
            text="नमस्ते",
            voice="InvalidVoice",
            language_code="hi",
        )

    assert "InvalidVoice" in str(exc_info.value)

    # The breaker for ("speak", "hi") must be OPEN
    cb = client.cb_registry.get_breaker("speak", "hi")
    assert cb.state == "OPEN"

    # Subsequent speak calls for this language must immediately fail fast
    with pytest.raises(CircuitBreakerOpen):
        await client.speak(
            text="अगला खंड",
            voice="InvalidVoice",
            language_code="hi",
        )

    # Exactly 1 network attempt was made; subsequent attempt was short-circuited
    assert tts_call_count == 1
    await client.close()


# ---------------------------------------------------------------------------
# C5: Validate --voices flag in preflight and CLI
# ---------------------------------------------------------------------------
def test_C5_validate_voices_mapping(tmp_path: Path):
    registry = LanguageRegistry()

    # Valid voice mapping string
    valid_map = validate_voices_mapping("hi:Kavya,bn:Ishita", registry)
    assert valid_map["hi"] == "Kavya"
    assert valid_map["bn"] == "Ishita"

    # Unknown language in voices
    with pytest.raises(PreflightError) as exc_info:
        validate_voices_mapping("xx:SomeVoice", registry)
    assert "Unsupported language 'xx'" in str(exc_info.value)

    # Unknown voice in valid language
    with pytest.raises(PreflightError) as exc_info:
        validate_voices_mapping("hi:invalid_voice", registry)
    assert "Unknown voice 'invalid_voice' for language 'hi'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_C5_cli_exits_2_on_invalid_voice(tmp_path: Path):
    video_path = tmp_path / "test.mp4"
    video_path.write_bytes(b"dummy")

    args = MagicMock()
    args.input = video_path
    args.langs = "hi"
    args.out = tmp_path / "out"
    args.voices = "hi:invalid_voice"
    args.estimate_only = False
    args.max_cost_inr = 100.0
    args.log_level = "INFO"

    exit_code = await async_main(args)
    assert exit_code == 2
