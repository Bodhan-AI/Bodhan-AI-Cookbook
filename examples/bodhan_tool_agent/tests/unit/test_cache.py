"""
Unit tests for content-addressed tool cache (src/bodhan_core/cache.py).
"""
from bodhan_core.cache import ToolCache


def test_cache_put_get(tmp_path):
    cache = ToolCache(tmp_path)
    k = ToolCache.make_key(b"sample_input_bytes", b"arg1")

    assert cache.has(k) is False
    assert cache.get(k) is None

    payload = b"cached_output_data"
    cache.put(k, payload)

    assert cache.has(k) is True
    assert cache.get(k) == payload


def test_cache_deterministic_key():
    k1 = ToolCache.make_key(b"hello", b"world")
    k2 = ToolCache.make_key(b"hello", b"world")
    k3 = ToolCache.make_key(b"hello", b"other")

    assert k1 == k2
    assert k1 != k3
