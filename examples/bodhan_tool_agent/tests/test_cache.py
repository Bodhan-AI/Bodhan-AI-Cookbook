"""Unit tests for the Zero-Dependency SQLite WAL Cache Layer (src.bodhan_mcp.cache).

Covers:
1. Canonicalization and content-addressed key generation (Unicode NFC, file hashing).
2. IndicCacheManager storage, retrieval, TTL invalidation, and LRU eviction hysteresis.
3. Concurrency safety under simultaneous asynchronous writes.
4. @indic_cache decorator behavior (Cache HIT/MISS, bypass, error quarantine).
5. Side-effect fidelity for binary assets (TTS WAV restoration).
6. Administrative tools (get_cache_stats, clear_cache).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.bodhan_mcp.cache import (
    CacheEntry,
    IndicCacheManager,
    canonicalize_text,
    get_cache_manager,
    hash_file_contents,
    indic_cache,
    make_extract_document_key,
    make_synthesize_key,
    make_transcribe_key,
    make_translate_key,
)
from src.bodhan_mcp.server import (
    clear_cache,
    get_cache_stats,
    synthesize_speech,
    translate_text,
)
from tests.test_audio import create_synthetic_wav


@pytest.fixture
def temp_cache_dir(tmp_path: Path):
    """Provides a fresh, isolated cache directory for tests."""
    cache_dir = tmp_path / "cache_test"
    cache_dir.mkdir(parents=True, exist_ok=True)
    manager = IndicCacheManager(cache_dir=cache_dir)
    return manager


# ============================================================================
# 1. Canonicalization and Key Generation Tests
# ============================================================================

def test_canonicalize_text_nfc():
    """Verify Unicode NFC normalization produces identical strings."""
    # Decomposed: 'e' followed by combining acute accent U+0301
    decomposed = "e\u0301"
    # Precomposed: 'é' U+00E9
    precomposed = "\u00e9"
    assert decomposed != precomposed
    assert canonicalize_text(decomposed) == canonicalize_text(precomposed)

    # Indic Devanagari conjunct normalization
    # 'क़' can be written as U+0915 U+093C or precomposed U+0958
    indic_decomposed = "\u0915\u093c"
    indic_precomposed = "\u0958"
    assert canonicalize_text(indic_decomposed) == canonicalize_text(indic_precomposed)


def test_canonicalize_text_line_endings():
    """Verify line endings (\r\n vs \n) and trailing line spaces normalize."""
    text_crlf = "Line 1  \r\nLine 2 \r\n"
    text_lf = "Line 1\nLine 2"
    assert canonicalize_text(text_crlf) == canonicalize_text(text_lf)


def test_hash_file_contents_streaming(tmp_path: Path):
    """Verify streaming SHA-256 chunk hashing matches standard hashlib."""
    import hashlib
    f = tmp_path / "test.bin"
    content = b"Bodhan AI streaming test content" * 5000
    f.write_bytes(content)

    expected_hash = hashlib.sha256(content).hexdigest()
    expected_size = len(content)

    h, sz = hash_file_contents(f, chunk_size=1024)
    assert h == expected_hash
    assert sz == expected_size


def test_key_generation_content_addressed_audio(tmp_path: Path):
    """Two different file paths with identical audio content must yield identical keys."""
    wav1 = create_synthetic_wav(tmp_path / "a.wav", duration_sec=1.0)
    wav2 = tmp_path / "b.wav"
    wav2.write_bytes(wav1.read_bytes())

    key1 = make_transcribe_key(file_path=wav1, language="hi", task="transcribe")
    key2 = make_transcribe_key(file_path=wav2, language="hi", task="transcribe")
    assert key1 == key2

    # Different language or task must produce different key
    key3 = make_transcribe_key(file_path=wav1, language="ta", task="transcribe")
    assert key1 != key3


def test_key_generation_synthesize_speech_ignores_output_path():
    """synthesize_speech cache key depends only on text, voice, style, lang, not output_path."""
    k1 = make_synthesize_key(text="नमस्ते", language="hi", voice="Kavya", style="news", output_path="a.wav")
    k2 = make_synthesize_key(text="नमस्ते", language="hi", voice="Kavya", style="news", output_path="b.wav")
    assert k1 == k2

    # Different style or voice produces distinct key
    k3 = make_synthesize_key(text="नमस्ते", language="hi", voice="Kavya", style="happy")
    assert k1 != k3


def test_key_generation_document(tmp_path: Path):
    """extract_document_text key hashes content and checks parameters."""
    doc = tmp_path / "doc.txt"
    doc.write_text("Hello document", encoding="utf-8")

    k1 = make_extract_document_key(file_path=doc, table_format="markdown", page_limit=2)
    k2 = make_extract_document_key(file_path=doc, table_format="markdown", page_limit=2)
    k3 = make_extract_document_key(file_path=doc, table_format="html", page_limit=2)
    assert k1 == k2
    assert k1 != k3


# ============================================================================
# 2. IndicCacheManager Operations & TTL Invalidation
# ============================================================================

def test_cache_put_get_and_stats(temp_cache_dir: IndicCacheManager):
    mgr = temp_cache_dir
    key = "test_key_123"
    mgr.put(
        key=key,
        tool_name="translate_text",
        input_summary="Hello world",
        response_data="வணக்கம் உலகம்",
        execution_time_ms=150.0,
    )

    entry = mgr.get(key)
    assert entry is not None
    assert entry.key == key
    assert entry.tool_name == "translate_text"
    assert entry.response_data == "வணக்கம் உலகம்"
    assert entry.access_count == 2  # initial 1 on put, +1 on get
    assert entry.execution_time_ms == 150.0

    stats = mgr.get_stats()
    assert stats["total_entries"] == 1
    assert stats["total_hits"] == 1
    assert stats["total_misses"] == 0
    assert stats["hit_ratio_percent"] == 100.0


def test_cache_miss_increments_stats(temp_cache_dir: IndicCacheManager):
    mgr = temp_cache_dir
    entry = mgr.get("non_existent_key")
    assert entry is None

    stats = mgr.get_stats()
    assert stats["total_misses"] == 1
    assert stats["total_hits"] == 0


def test_cache_ttl_expiration(tmp_path: Path):
    """Entries past TTL must be deleted and counted as misses."""
    cache_dir = tmp_path / "ttl_test"
    mgr = IndicCacheManager(cache_dir=cache_dir)
    # Set short TTL
    mgr.ttl_seconds = 0.2  # 200 ms

    key = "expiring_key"
    mgr.put(key=key, tool_name="test_tool", input_summary="ttl test", response_data="valid")

    # Immediate access succeeds
    assert mgr.get(key) is not None

    # Wait for TTL to expire
    time.sleep(0.3)
    assert mgr.get(key) is None  # Expired -> deleted and counted as miss

    # Verify deleted from DB
    stats = mgr.get_stats()
    assert stats["total_entries"] == 0


def test_cache_clear(temp_cache_dir: IndicCacheManager):
    mgr = temp_cache_dir
    mgr.put("k1", "translate_text", "s1", "r1")
    mgr.put("k2", "translate_text", "s2", "r2")
    mgr.put("k3", "transcribe_audio", "s3", "r3")

    assert mgr.get_stats()["total_entries"] == 3

    # Clear specific tool
    deleted = mgr.clear("translate_text")
    assert deleted == 2
    assert mgr.get_stats()["total_entries"] == 1
    assert mgr.get("k3") is not None

    # Clear all
    all_deleted = mgr.clear()
    assert all_deleted == 1
    assert mgr.get_stats()["total_entries"] == 0


# ============================================================================
# 3. Dual-Constraint LRU Eviction with Hysteresis
# ============================================================================

def test_lru_eviction_max_entries(tmp_path: Path):
    """When entries breach max_entries, oldest are pruned to 85% low watermark."""
    cache_dir = tmp_path / "lru_entries_test"
    mgr = IndicCacheManager(cache_dir=cache_dir)
    mgr.max_entries = 10
    mgr.max_bytes = 100_000_000  # large bytes budget so only count triggers

    # Insert 10 entries (capacity at 100%)
    for i in range(10):
        mgr.put(f"k{i}", "test", f"item {i}", f"data {i}")
        time.sleep(0.01)

    assert mgr.get_stats()["total_entries"] == 10

    # 11th entry triggers eviction to 85% of 10 = 8 entries.
    # Total entries after pruning should be <= 8 + 1 = 8.
    mgr.put("k10", "test", "item 10", "data 10")

    stats = mgr.get_stats()
    assert stats["total_entries"] <= 8

    # The oldest entries (k0, k1, k2) should have been evicted
    assert mgr.get("k0") is None
    assert mgr.get("k1") is None
    # Recent entry should still exist
    assert mgr.get("k10") is not None


def test_lru_eviction_max_bytes(tmp_path: Path):
    """When size breaches max_bytes, oldest are pruned to 85% low watermark."""
    cache_dir = tmp_path / "lru_bytes_test"
    mgr = IndicCacheManager(cache_dir=cache_dir)
    mgr.max_entries = 1000
    # Set small byte limit: 5000 bytes
    mgr.max_bytes = 5000

    # Each payload is ~1000 bytes
    payload = "x" * 1000
    for i in range(5):
        mgr.put(f"k{i}", "test", f"item {i}", payload)
        time.sleep(0.01)

    # 6th entry breaches 5000 bytes
    mgr.put("k5", "test", "item 5", payload)

    stats = mgr.get_stats()
    # 85% of 5000 = 4250 bytes
    assert stats["total_size_bytes"] <= 4250
    # Oldest entry evicted
    assert mgr.get("k0") is None
    assert mgr.get("k5") is not None


# ============================================================================
# 4. Concurrency Safety (Async SQLite WAL)
# ============================================================================

@pytest.mark.asyncio
async def test_concurrent_async_cache_access(tmp_path: Path):
    """Multiple concurrent async tasks can read and write without database locks."""
    cache_dir = tmp_path / "concurrent_cache"
    mgr = IndicCacheManager(cache_dir=cache_dir)

    async def worker(worker_id: int):
        for i in range(10):
            key = f"key_{worker_id}_{i}"
            mgr.put(key, "worker_tool", f"summary {worker_id}", f"result_{worker_id}_{i}", execution_time_ms=5.0)
            await asyncio.sleep(0.001)
            entry = mgr.get(key)
            assert entry is not None
            assert entry.response_data == f"result_{worker_id}_{i}"

    tasks = [worker(w) for w in range(8)]
    await asyncio.gather(*tasks)

    stats = mgr.get_stats()
    assert stats["total_entries"] == 80
    assert stats["total_hits"] == 80


# ============================================================================
# 5. @indic_cache Decorator & Error Isolation
# ============================================================================

@pytest.mark.asyncio
async def test_indic_cache_decorator_miss_then_hit(tmp_path: Path):
    """Verify decorator hits cache on second call and saves execution time."""
    cache_dir = tmp_path / "dec_test"
    IndicCacheManager.reset_instance()
    mgr = IndicCacheManager.get_instance(cache_dir=cache_dir)

    call_count = 0

    def mock_key(text: str, **kwargs):
        return f"mock_{text}"

    @indic_cache(tool_name="mock_tool", key_generator=mock_key)
    async def sample_tool(text: str, bypass_cache: bool = False) -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return f"processed: {text}"

    # Call 1: MISS
    res1 = await sample_tool(text="apple")
    assert res1 == "processed: apple"
    assert call_count == 1

    # Call 2: HIT (call_count does not increase)
    res2 = await sample_tool(text="apple")
    assert res2 == "processed: apple"
    assert call_count == 1

    stats = mgr.get_stats()
    assert stats["total_hits"] == 1
    assert stats["total_misses"] == 1


@pytest.mark.asyncio
async def test_indic_cache_bypass(tmp_path: Path):
    """Calling with bypass_cache=True executes live function even if cached."""
    cache_dir = tmp_path / "bypass_test"
    IndicCacheManager.reset_instance()
    mgr = IndicCacheManager.get_instance(cache_dir=cache_dir)

    call_count = 0

    def mock_key(val: str, **kwargs):
        return f"bypass_{val}"

    @indic_cache(tool_name="bypass_tool", key_generator=mock_key)
    async def echo_tool(val: str, bypass_cache: bool = False) -> str:
        nonlocal call_count
        call_count += 1
        return f"val_{val}_{call_count}"

    # First call stores val_hello_1
    res1 = await echo_tool(val="hello")
    assert res1 == "val_hello_1"
    assert call_count == 1

    # Normal second call hits cache
    res2 = await echo_tool(val="hello")
    assert res2 == "val_hello_1"
    assert call_count == 1

    # Call with bypass_cache=True re-executes function
    res3 = await echo_tool(val="hello", bypass_cache=True)
    assert res3 == "val_hello_2"
    assert call_count == 2

    # Stats verify bypass was tracked
    stats = mgr.get_stats()
    assert stats["total_bypasses"] == 1


@pytest.mark.asyncio
async def test_errors_are_never_cached(tmp_path: Path):
    """ToolError and exceptions must NEVER be stored in the cache."""
    cache_dir = tmp_path / "error_quarantine_test"
    IndicCacheManager.reset_instance()
    mgr = IndicCacheManager.get_instance(cache_dir=cache_dir)

    attempts = 0

    def mock_key(text: str, **kwargs):
        return f"err_{text}"

    @indic_cache(tool_name="fail_tool", key_generator=mock_key)
    async def failing_tool(text: str, bypass_cache: bool = False) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ToolError(f"Temporary failure {attempts}")
        return "success finally"

    # First attempt fails
    with pytest.raises(ToolError) as exc1:
        await failing_tool(text="retry_me")
    assert "Temporary failure 1" in str(exc1.value)

    # Cache should be completely empty (error was NOT stored)
    assert mgr.get_stats()["total_entries"] == 0

    # Second attempt fails
    with pytest.raises(ToolError) as exc2:
        await failing_tool(text="retry_me")
    assert "Temporary failure 2" in str(exc2.value)
    assert mgr.get_stats()["total_entries"] == 0

    # Third attempt succeeds -> now it should be stored
    res = await failing_tool(text="retry_me")
    assert res == "success finally"
    assert mgr.get_stats()["total_entries"] == 1


# ============================================================================
# 6. Binary Asset Side-Effect Restoration (synthesize_speech)
# ============================================================================

@pytest.mark.asyncio
async def test_binary_asset_restoration_on_cache_hit(tmp_path: Path):
    """When a binary asset (WAV) is cached, a cache hit must restore the file on disk."""
    cache_dir = tmp_path / "tts_cache"
    IndicCacheManager.reset_instance()
    mgr = IndicCacheManager.get_instance(cache_dir=cache_dir)

    synthetic_wav_data = create_synthetic_wav(tmp_path / "seed.wav", duration_sec=1.5).read_bytes()
    calls = 0

    def tts_key(text: str, language: str, **kwargs):
        return make_synthesize_key(text=text, language=language, **kwargs)

    @indic_cache(tool_name="synthesize_speech", key_generator=tts_key, is_binary_asset=True)
    async def mock_synthesize(
        text: str,
        language: str,
        output_path: str | None = None,
        bypass_cache: bool = False,
    ) -> str:
        nonlocal calls
        calls += 1
        dest = Path(output_path or (tmp_path / f"speech_{language}.wav"))
        dest.write_bytes(synthetic_wav_data)
        return json.dumps({
            "status": "success",
            "output_file": str(dest),
            "duration_seconds": 1.5,
            "sample_rate": 24000,
            "language": language,
        })

    target_wav = tmp_path / "restored.wav"

    # 1. First execution writes WAV and caches binary blob
    res1_str = await mock_synthesize(text="नमस्ते भारत", language="hi", output_path=str(target_wav))
    res1 = json.loads(res1_str)
    assert calls == 1
    assert target_wav.is_file()
    assert target_wav.stat().st_size == len(synthetic_wav_data)

    # 2. Delete the physical WAV file from disk
    target_wav.unlink()
    assert not target_wav.exists()

    # 3. Second execution (Cache HIT) must restore the physical WAV file
    res2_str = await mock_synthesize(text="नमस्ते भारत", language="hi", output_path=str(target_wav))
    res2 = json.loads(res2_str)
    assert calls == 1  # Did NOT call live synthesis
    assert res2.get("cached") is True
    assert target_wav.is_file(), "Binary WAV file was not restored on cache hit!"
    assert target_wav.stat().st_size == len(synthetic_wav_data)


# ============================================================================
# 7. FastMCP Administrative Tools Integration
# ============================================================================

@pytest.mark.asyncio
async def test_admin_mcp_tools():
    """Verify get_cache_stats and clear_cache MCP tools return valid JSON."""
    stats_raw = await get_cache_stats()
    stats = json.loads(stats_raw)
    assert stats["enabled"] is True
    assert "total_entries" in stats
    assert "total_hits" in stats

    clear_raw = await clear_cache()
    clear_res = json.loads(clear_raw)
    assert clear_res["status"] == "success"
    assert "cleared_entries" in clear_res
