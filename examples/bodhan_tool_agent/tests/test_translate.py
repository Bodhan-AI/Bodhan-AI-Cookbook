"""Tests for Indic-Translate MCP tool using httpx.MockTransport (Zero Live API Calls)."""

import json
from pathlib import Path
import httpx
import pytest

from bodhan_mcp.tools.translate import (
    IndicTranslateTool,
    chunk_text,
    PRICE_PER_10K_OUTPUT_TOKENS_INR,
)


@pytest.fixture
def tmp_cache(tmp_path: Path):
    """Provides an isolated temporary cache directory for each test."""
    cache_dir = tmp_path / "test_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@pytest.mark.asyncio
async def test_unsupported_language_makes_zero_http_calls(tmp_cache: Path):
    """Unsupported target or source language must return structured error before making any HTTP call."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranslateTool(api_key="test_key", client=mock_client, cache_dir=tmp_cache)

    result = await tool.translate(
        text="Hello world",
        target_language="French",  # Unsupported
        source_language="en",
    )

    assert call_count == 0
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "UNSUPPORTED_LANGUAGE"
    assert "French" in result.error.message


@pytest.mark.asyncio
async def test_cache_hit_makes_zero_http_calls(tmp_cache: Path):
    """Exact duplicate translation must be served from disk cache without making second HTTP call."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        data = {
            "choices": [{"message": {"role": "assistant", "content": "नमस्ते दुनिया"}}],
            "truncated": False,
            "usage": {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11},
        }
        return httpx.Response(200, json=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranslateTool(api_key="test_key", client=mock_client, cache_dir=tmp_cache)

    # First call: cache miss, hits mock server
    res1 = await tool.translate("Hello world", target_language="hi", source_language="en")
    assert res1.success is True
    assert res1.cache_hit is False
    assert res1.translated_text == "नमस्ते दुनिया"
    assert call_count == 1

    # Second call: cache hit, zero new HTTP calls
    res2 = await tool.translate("Hello world", target_language="hi", source_language="en")
    assert res2.success is True
    assert res2.cache_hit is True
    assert res2.translated_text == "नमस्ते दुनिया"
    assert call_count == 1  # Still 1


@pytest.mark.asyncio
async def test_indic_to_indic_pivot_path(tmp_cache: Path):
    """Indic to Indic directions (e.g. Hindi to Tamil) must pivot through English and record both calls."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append(payload)
        target = payload.get("target_language_code")
        if target == "en":
            resp_text = "Good morning"
        else:
            resp_text = "காலை வணக்கம்"

        data = {
            "choices": [{"message": {"role": "assistant", "content": resp_text}}],
            "truncated": False,
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        return httpx.Response(200, json=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranslateTool(api_key="test_key", client=mock_client, cache_dir=tmp_cache)

    result = await tool.translate("शुभ प्रभात", target_language="ta", source_language="hi")

    assert result.success is True
    assert result.pivoted_via_english is True
    assert result.translated_text == "காலை வணக்கம்"
    assert len(calls) == 2
    # Verify no source_language_code in payload, target_language_code is sent
    assert "source_language_code" not in calls[0]
    assert calls[0]["target_language_code"] == "en"
    assert "source_language_code" not in calls[1]
    assert calls[1]["target_language_code"] == "ta"


@pytest.mark.asyncio
async def test_chunking_large_text(tmp_cache: Path):
    """Large text exceeding chunk limit must be split and translated in chunks."""
    paragraphs = [f"Paragraph {i}: This is sentence {i} explaining topic {i}." for i in range(4)]
    full_text = "\n\n".join(paragraphs)

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        payload = json.loads(request.content.decode("utf-8"))
        src = payload["messages"][0]["content"]
        data = {
            "choices": [{"message": {"role": "assistant", "content": f"अनुवाद: {src[:20]}"}}],
            "truncated": False,
            "usage": {"prompt_tokens": 15, "completion_tokens": 15, "total_tokens": 30},
        }
        return httpx.Response(200, json=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranslateTool(
        api_key="test_key",
        client=mock_client,
        cache_dir=tmp_cache,
        max_chunk_chars=60,
        max_http_calls=10,
    )

    chunks = chunk_text(full_text, max_chars=120)
    assert len(chunks) > 1

    result = await tool.translate(full_text, target_language="hi", source_language="en")
    assert result.success is True
    assert result.chunk_count > 1
    assert call_count == result.chunk_count


@pytest.mark.asyncio
async def test_truncated_handling(tmp_cache: Path):
    """If truncated flag is True, tool attempts sub-split and retry."""
    attempt = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        payload = json.loads(request.content.decode("utf-8"))
        content = payload["messages"][0]["content"]

        if attempt == 1:
            data = {
                "choices": [{"message": {"role": "assistant", "content": "आंशिक अनुवाद..."}}],
                "truncated": True,
                "usage": {"prompt_tokens": 50, "completion_tokens": 50, "total_tokens": 100},
            }
        else:
            data = {
                "choices": [{"message": {"role": "assistant", "content": f"सफल: {content[:10]}"}}],
                "truncated": False,
                "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
            }
        return httpx.Response(200, json=data)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranslateTool(api_key="test_key", client=mock_client, cache_dir=tmp_cache)

    long_sentence = "Sentence one is clear. Sentence two is also very clear and descriptive."
    result = await tool.translate(long_sentence, target_language="hi", source_language="en")

    assert result.success is True
    assert attempt >= 2  # Proves retry with smaller split was executed


@pytest.mark.asyncio
async def test_rate_limit_429_retry(tmp_cache: Path):
    """Tool must honour 429 rate limit with retry-after header and succeed on retry."""
    attempt = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(
                429, headers={"retry-after": "0.01"}, json={"error": "Too Many Requests"}
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "सफलता"}}],
                "truncated": False,
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranslateTool(api_key="test_key", client=mock_client, cache_dir=tmp_cache)

    result = await tool.translate("Success", target_language="hi", source_language="en")

    assert result.success is True
    assert result.translated_text == "सफलता"
    assert attempt == 2


@pytest.mark.asyncio
async def test_truncated_and_errors_not_cached(tmp_cache: Path):
    """A persistent truncated response or HTTP error must NEVER be written to disk cache."""

    # Case 1: Persistent truncation
    def trunc_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "truncated text"}}],
                "truncated": True,
                "usage": {"prompt_tokens": 50, "completion_tokens": 50, "total_tokens": 100},
            },
        )

    mock_client_trunc = httpx.AsyncClient(transport=httpx.MockTransport(trunc_handler))
    tool_trunc = IndicTranslateTool(
        api_key="test_key", client=mock_client_trunc, cache_dir=tmp_cache
    )
    res_trunc = await tool_trunc.translate(
        "Short input that always truncates", target_language="hi"
    )

    assert res_trunc.success is False
    assert res_trunc.error.code == "OUTPUT_TRUNCATED"
    # Ensure cache directory is empty
    cache_files = list(tmp_cache.glob("*.json"))
    assert len(cache_files) == 0, f"Cache contains unexpected files on truncation: {cache_files}"

    # Case 2: HTTP 500 error
    def err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal Server Error"})

    mock_client_err = httpx.AsyncClient(transport=httpx.MockTransport(err_handler))
    tool_err = IndicTranslateTool(api_key="test_key", client=mock_client_err, cache_dir=tmp_cache)
    res_err = await tool_err.translate("Input for error", target_language="hi")

    assert res_err.success is False
    assert res_err.error.code == "SERVER_ERROR"
    cache_files_after_err = list(tmp_cache.glob("*.json"))
    assert len(cache_files_after_err) == 0, "Cache contains unexpected files on HTTP error"


@pytest.mark.asyncio
async def test_truncation_depth_and_call_cap(tmp_cache: Path):
    """Recursion depth is capped at 2 and total HTTP calls per tool invocation are capped at 8."""
    call_count = 0

    def always_trunc_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "trunc"}}],
                "truncated": True,
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(always_trunc_handler))
    # Test with call cap = 5
    tool = IndicTranslateTool(
        api_key="test_key",
        client=mock_client,
        cache_dir=tmp_cache,
        max_http_calls=5,
    )

    multi_sentence = (
        "One sentence here. Another sentence here. Third sentence here. Fourth sentence here."
    )
    result = await tool.translate(multi_sentence, target_language="hi")

    assert result.success is False
    # Proves calls never exceed the cap
    assert call_count <= 5
    assert result.error is not None
    assert result.error.code in ("CALL_LIMIT_EXCEEDED", "OUTPUT_TRUNCATED")


@pytest.mark.asyncio
async def test_cost_sum_over_chunks_and_pivot(tmp_cache: Path):
    """completion_tokens and estimated_cost_inr must be correctly summed over chunks and pivot steps."""
    call_index = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_index
        call_index += 1
        # Return 500 completion tokens per call
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": f"Translation step {call_index}"}}
                ],
                "truncated": False,
                "usage": {"prompt_tokens": 100, "completion_tokens": 500, "total_tokens": 600},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranslateTool(api_key="test_key", client=mock_client, cache_dir=tmp_cache)

    # Indic-to-Indic pivot: 2 calls (step 1: hi->en, step 2: en->ta)
    # Each returns 500 completion tokens -> total 1000 completion tokens
    # Cost = (1000 / 10000) * 0.20 = 0.02 INR
    result = await tool.translate("शुभ प्रभात", target_language="ta", source_language="hi")

    assert result.success is True
    assert result.pivoted_via_english is True
    assert result.completion_tokens == 1000
    expected_cost = (1000 / 10000.0) * PRICE_PER_10K_OUTPUT_TOKENS_INR
    assert abs(result.estimated_cost_inr - expected_cost) < 1e-6
    assert result.chunk_count == 2
    assert result.cache_hit is False


@pytest.mark.asyncio
async def test_truncation_detection_rules(tmp_cache: Path):
    """Missing 'truncated' key is not truncated when finish_reason='stop'; finish_reason='length' is truncated."""

    # Case A: Missing truncated key, finish_reason='stop' -> NOT truncated
    def handler_normal(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "पूर्ण अनुवाद"},
                    }
                ],
                "usage": {"completion_tokens": 10, "total_tokens": 20},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler_normal))
    tool = IndicTranslateTool(api_key="test_key", client=mock_client, cache_dir=tmp_cache)
    res_normal = await tool.translate("Short text", target_language="hi")
    assert res_normal.success is True
    assert res_normal.translated_text == "पूर्ण अनुवाद"

    # Case B: finish_reason='length' even without 'truncated' key -> Treated as truncated and retried
    calls = 0

    def handler_length(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"role": "assistant", "content": "आधा"},
                        }
                    ],
                    "usage": {"completion_tokens": 5, "total_tokens": 15},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"role": "assistant", "content": "पूर्ण"}}
                ],
                "usage": {"completion_tokens": 8, "total_tokens": 18},
            },
        )

    mock_client_len = httpx.AsyncClient(transport=httpx.MockTransport(handler_length))
    tool_len = IndicTranslateTool(api_key="test_key", client=mock_client_len, cache_dir=tmp_cache)
    res_len = await tool_len.translate("First sentence. Second sentence.", target_language="hi")
    assert res_len.success is True
    assert calls >= 2  # Proven that finish_reason='length' triggered retry


@pytest.mark.asyncio
async def test_discarded_truncated_tokens_are_billed(tmp_cache: Path):
    """Discarded tokens from truncated calls must be counted in completion_tokens and estimated_cost_inr."""
    call = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call
        call += 1
        if call == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"role": "assistant", "content": "discarded"},
                        }
                    ],
                    "usage": {"completion_tokens": 100, "total_tokens": 150},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "sub-translation"},
                    }
                ],
                "usage": {"completion_tokens": 50, "total_tokens": 70},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranslateTool(api_key="test_key", client=mock_client, cache_dir=tmp_cache)
    res = await tool.translate("Sentence alpha. Sentence beta.", target_language="hi")

    assert res.success is True
    # Discarded (100) + sub-chunks (50 * 2 = 100) = 200 completion tokens
    assert res.completion_tokens >= 150
    expected_cost = (res.completion_tokens / 10000.0) * PRICE_PER_10K_OUTPUT_TOKENS_INR
    assert abs(res.estimated_cost_inr - expected_cost) < 1e-6


@pytest.mark.asyncio
async def test_sub_chunks_cached_individually_on_retry(tmp_cache: Path):
    """Sub-chunks translated during retry must be stored in cache individually."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        payload = json.loads(request.content.decode("utf-8"))
        msg = payload["messages"][0]["content"]
        if call_count == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"role": "assistant", "content": "trunc"},
                        }
                    ],
                    "usage": {"completion_tokens": 10, "total_tokens": 20},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": f"अनुवाद-{msg[:5]}"},
                    }
                ],
                "usage": {"completion_tokens": 15, "total_tokens": 25},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = IndicTranslateTool(api_key="test_key", client=mock_client, cache_dir=tmp_cache)
    res = await tool.translate("Part one. Part two.", target_language="hi")
    assert res.success is True

    # Now translate "Part one." directly: should hit cache with 0 HTTP calls!
    calls_before = call_count
    res_sub = await tool.translate("Part one.", target_language="hi")
    assert res_sub.success is True
    assert res_sub.cache_hit is True
    assert call_count == calls_before  # No new HTTP calls made!
