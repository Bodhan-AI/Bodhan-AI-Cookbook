import pytest

from src.bodhan_mcp.server import translate_text
from src.bodhan_cache import CacheRecord, CacheStore, MemoryBackend, stable_key


def test_translate_text_rejects_empty_input():
    with pytest.raises(Exception, match="must not be empty"):
        translate_text("", "hi")


def test_cache_key_is_order_independent():
    first = stable_key("translate_text", "indic-translate", {"text": "hello", "target": "hi"})
    second = stable_key("translate_text", "indic-translate", {"target": "hi", "text": "hello"})
    assert first == second


def test_memory_cache_round_trip():
    cache = CacheStore(MemoryBackend())
    cache.set("key", CacheRecord(result={"value": 1}))
    assert cache.get("key").result == {"value": 1}
