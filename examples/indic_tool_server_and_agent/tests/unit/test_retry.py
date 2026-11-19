"""Unit tests for Bodhan retry mechanism and error classifications."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from bodhan_mcp.core.errors import (
    BodhanError,
    RATE_LIMITED,
    TIMEOUT,
    UPSTREAM_ERROR,
    INVALID_INPUT,
)
from bodhan_mcp.core.retry import bodhan_retry, _is_retryable


def test_is_retryable_classification():
    """Verify which BodhanErrors are classified as retryable."""
    rate_err = BodhanError(RATE_LIMITED, "Too many requests", retryable=True)
    timeout_err = BodhanError(TIMEOUT, "Timed out", retryable=True)
    upstream_err = BodhanError(UPSTREAM_ERROR, "Server 500", retryable=True)
    invalid_err = BodhanError(INVALID_INPUT, "Bad parameters", retryable=False)

    assert _is_retryable(rate_err) is True
    assert _is_retryable(timeout_err) is True
    assert _is_retryable(upstream_err) is True
    assert _is_retryable(invalid_err) is False
    assert _is_retryable(ValueError("arbitrary")) is False


@pytest.mark.asyncio
async def test_retry_on_429_then_success():
    """429 Rate limited on first attempt succeeds on second attempt."""
    attempts = 0

    @bodhan_retry
    async def sample_call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BodhanError(RATE_LIMITED, "Rate limit 429", retryable=True)
        return {"ok": True, "result": "success"}

    # Mock asyncio.sleep to fast-forward backoff
    with patch("asyncio.sleep", return_value=None):
        res = await sample_call()

    assert res["ok"] is True
    assert attempts == 2


@pytest.mark.asyncio
async def test_retry_on_500_then_success():
    """500 Server error on first attempt succeeds on second attempt."""
    attempts = 0

    @bodhan_retry
    async def sample_call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BodhanError(UPSTREAM_ERROR, "Internal server error 500", retryable=True)
        return {"data": "recovered"}

    with patch("asyncio.sleep", return_value=None):
        res = await sample_call()

    assert res["data"] == "recovered"
    assert attempts == 2


@pytest.mark.asyncio
async def test_non_retryable_400_fails_immediately():
    """400 Invalid input (retryable=False) is never retried."""
    attempts = 0

    @bodhan_retry
    async def bad_call():
        nonlocal attempts
        attempts += 1
        raise BodhanError(INVALID_INPUT, "Malformed JSON", retryable=False)

    with patch("asyncio.sleep", return_value=None):
        with pytest.raises(BodhanError) as exc_info:
            await bad_call()

    assert exc_info.value.code == INVALID_INPUT
    assert attempts == 1  # No retry attempted


@pytest.mark.asyncio
async def test_retry_exceeds_max_attempts():
    """Persistent retryable error raises after max attempts (3)."""
    attempts = 0

    @bodhan_retry
    async def always_timeout():
        nonlocal attempts
        attempts += 1
        raise BodhanError(TIMEOUT, "Connection timed out", retryable=True)

    with patch("asyncio.sleep", return_value=None):
        with pytest.raises(BodhanError) as exc_info:
            await always_timeout()

    assert exc_info.value.code == TIMEOUT
    assert attempts == 3
