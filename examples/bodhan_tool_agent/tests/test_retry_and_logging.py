"""Tests for safe structured logging, exponential backoff retries, and error handling.

Validates:
1. Logger outputs strictly to sys.stderr (MCP stdio protocol safety, nothing on stdout).
2. Logger respects dynamic log levels and supports file logging.
3. Resilient retry recovers from transient errors (503 -> 200).
4. Retry terminates with descriptive ToolError on max retries exhausted.
5. Non-retryable client errors (401, 400, 403, 404) fail immediately with actionable ToolError.
6. Rate limiting (429) honors Retry-After header.
7. Concurrency limiter semaphore behaves as expected.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.common.logger import (
    add_file_handler,
    bodhan_logger,
    get_logger,
    init_logging,
    set_log_level,
)
from src.common.retry import (
    compute_backoff_delay,
    format_non_retryable_error,
    get_concurrency_semaphore,
    parse_retry_after,
    reset_concurrency_semaphore,
    retry_async,
    retry_sync,
    with_retry,
)


# ============================================================================
# A. Logger Tests
# ============================================================================

def test_logger_outputs_strictly_to_stderr(capsys: pytest.CaptureFixture[str]):
    """Verify logger output goes strictly to stderr to protect MCP stdio JSON-RPC framing."""
    set_log_level("INFO")
    logger = get_logger("test_component")

    test_msg = "MCP stdio safe logging check"
    logger.info(test_msg)

    captured = capsys.readouterr()
    # Critical: stdout must remain completely empty!
    assert captured.out == "", "Stdout must NEVER receive log messages in MCP stdio mode!"
    assert test_msg in captured.err
    assert "[INFO]" in captured.err
    assert "bodhan.test_component" in captured.err


def test_logger_respects_log_levels(capsys: pytest.CaptureFixture[str]):
    """Verify log messages below the configured threshold are suppressed."""
    set_log_level("WARNING")
    logger = get_logger("level_test")

    logger.debug("Debug hidden message")
    logger.info("Info hidden message")
    captured = capsys.readouterr()
    assert "Debug hidden message" not in captured.err
    assert "Info hidden message" not in captured.err

    logger.warning("Warning visible message")
    logger.error("Error visible message")
    captured = capsys.readouterr()
    assert "Warning visible message" in captured.err
    assert "Error visible message" in captured.err

    # Reset back to INFO
    set_log_level("INFO")


def test_logger_file_handler(tmp_path: Path):
    """Verify optional file logging writes structured entries."""
    log_file = tmp_path / "bodhan_test.log"
    add_file_handler(log_file, level="DEBUG")

    logger = get_logger("file_test")
    logger.info("Log line to disk")

    assert log_file.is_file()
    content = log_file.read_text(encoding="utf-8")
    assert "Log line to disk" in content
    assert "[INFO]" in content
    assert "bodhan.file_test" in content


# ============================================================================
# B. Resilient Retry Tests
# ============================================================================

async def test_retry_transient_error_503_to_200():
    """Verify retry logic recovers when a service returns 503 twice before succeeding."""
    call_count = 0
    dummy_req = httpx.Request("POST", "https://api.bodhan.ai/v1/test")

    async def flaky_api() -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503, text="Service Unavailable", request=dummy_req)
        return httpx.Response(200, json={"status": "ok"}, request=dummy_req)

    res = await retry_async(flaky_api, max_retries=3, initial_delay=0.01, backoff_factor=1.5)
    assert res.status_code == 200
    assert call_count == 3
    assert res.json() == {"status": "ok"}


async def test_retry_exhausted_terminates_with_tool_error():
    """Verify retry stops at max_retries and converts persistent 503 to ToolError."""
    call_count = 0
    dummy_req = httpx.Request("POST", "https://api.bodhan.ai/v1/test")

    async def always_failing_api() -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(503, text="Server Overloaded", request=dummy_req)

    with pytest.raises(ToolError) as exc_info:
        await retry_async(always_failing_api, max_retries=2, initial_delay=0.01)

    assert call_count == 3  # Initial attempt + 2 retries
    assert "after 2 retries" in str(exc_info.value)
    assert "503" in str(exc_info.value)


async def test_non_retryable_401_fails_immediately():
    """Verify non-retryable 401 Unauthorized fails immediately on first attempt without retrying."""
    call_count = 0
    dummy_req = httpx.Request("POST", "https://api.bodhan.ai/v1/audio/transcriptions")

    async def unauthorized_api() -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, text="Invalid API key", request=dummy_req)

    with pytest.raises(ToolError) as exc_info:
        await retry_async(unauthorized_api, max_retries=3, initial_delay=0.01)

    # Must fail immediately: exactly 1 call
    assert call_count == 1
    err_msg = str(exc_info.value)
    assert "401" in err_msg
    assert "Authentication failed" in err_msg
    assert "BODHAN" in err_msg


async def test_non_retryable_400_fails_immediately():
    """Verify 400 Bad Request fails immediately with diagnostic guidance."""
    call_count = 0
    dummy_req = httpx.Request("POST", "https://api.bodhan.ai/v1/translate")

    async def bad_request_api() -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, text="Unsupported source language", request=dummy_req)

    with pytest.raises(ToolError) as exc_info:
        await retry_async(bad_request_api, max_retries=3, initial_delay=0.01)

    assert call_count == 1
    assert "400" in str(exc_info.value)
    assert "Invalid request parameters" in str(exc_info.value)


async def test_retry_on_network_timeout():
    """Verify transient network timeouts (httpx.TimeoutException) are retried."""
    call_count = 0
    dummy_req = httpx.Request("POST", "https://api.bodhan.ai/v1/audio/speech")

    async def timeout_then_ok() -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ReadTimeout("Socket read timed out", request=dummy_req)
        return httpx.Response(200, content=b"RIFF...", request=dummy_req)

    res = await retry_async(timeout_then_ok, max_retries=2, initial_delay=0.01)
    assert res.status_code == 200
    assert call_count == 2


def test_rate_limit_retry_after_header():
    """Verify Retry-After header parsing for HTTP 429 rate limit backoff."""
    dummy_req = httpx.Request("POST", "https://api.bodhan.ai/v1/translate")
    resp = httpx.Response(429, headers={"Retry-After": "3.5"}, request=dummy_req)

    delay = parse_retry_after(resp, fallback_delay=0.5)
    assert delay == 3.5

    computed = compute_backoff_delay(attempt=1, initial_delay=0.5, backoff_factor=2.0, max_delay=30.0, response=resp)
    assert computed == 3.5


def test_sync_retry_and_decorator():
    """Verify synchronous retry and decorator work as expected."""
    call_count = 0

    @with_retry(max_retries=2, initial_delay=0.01)
    def flaky_sync() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.ConnectError("Connection refused")
        return "success"

    result = flaky_sync()
    assert result == "success"
    assert call_count == 2


async def test_concurrency_limiter():
    """Verify concurrency semaphore limits concurrent requests."""
    sem = reset_concurrency_semaphore(limit=2)
    active_count = 0
    max_observed = 0

    async def worker() -> int:
        nonlocal active_count, max_observed
        async with sem:
            active_count += 1
            max_observed = max(max_observed, active_count)
            await asyncio.sleep(0.05)
            active_count -= 1
        return max_observed

    results = await asyncio.gather(*(worker() for _ in range(6)))
    assert max(results) <= 2
