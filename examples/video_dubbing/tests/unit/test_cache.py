"""
Unit tests for content-addressed DiskCache.
"""

from bodhan_dub.gateway.cache import DiskCache


def test_cache_compute_key_deterministic(tmp_path):
    cache = DiskCache(cache_dir=tmp_path, version="v1")
    k1 = cache.compute_key("asr", "core", {"lang": "en", "opt": 1}, b"audio123")
    k2 = cache.compute_key("asr", "core", {"opt": 1, "lang": "en"}, b"audio123")
    assert k1 == k2, "Key computation must be invariant to param dictionary ordering"

    k3 = cache.compute_key("asr", "core", {"lang": "hi"}, b"audio123")
    assert k1 != k3


def test_cache_put_and_get_json(tmp_path):
    cache = DiskCache(cache_dir=tmp_path, version="v1")
    key = cache.compute_key("translate", "indic-translate", {"target": "hi"}, "hello")

    assert cache.get(key) is None

    payload = {"choices": [{"message": {"content": "नमस्ते"}}]}
    cache.put(key, payload, cost=0.005)

    cached_entry = cache.get(key)
    assert cached_entry is not None
    data, cost = cached_entry
    assert data == payload
    assert cost == 0.005


def test_cache_put_and_get_binary(tmp_path):
    cache = DiskCache(cache_dir=tmp_path, version="v1")
    key = cache.compute_key("speak", "indic-speak", {"voice": "Kavya"}, "नमस्ते")

    raw_wav = b"RIFF1234WAVEfmt "
    cache.put(key, raw_wav, cost=0.012)

    cached_entry = cache.get(key)
    assert cached_entry is not None
    data, cost = cached_entry
    assert data == raw_wav
    assert cost == 0.012
