from __future__ import annotations

from bodhan_dub.cache import Cache


def test_roundtrip(tmp_path) -> None:
    cache = Cache(tmp_path / "c")
    key = Cache.make_key("model", "hi", b"payload")

    assert cache.get(key) is None
    cache.put(key, {"text": "नमस्ते"}, {"model": "indic-transcribe"})

    entry = cache.get(key)
    assert entry is not None
    assert entry.data["text"] == "नमस्ते"
    assert entry.meta["model"] == "indic-transcribe"


def test_key_is_deterministic_and_order_sensitive() -> None:
    assert Cache.make_key("a", "b") == Cache.make_key("a", "b")
    assert Cache.make_key("a", "b") != Cache.make_key("b", "a")
    assert Cache.make_key("ab") != Cache.make_key("a", "b")


def test_key_handles_bytes_and_str() -> None:
    assert Cache.make_key("model", b"raw") == Cache.make_key("model", b"raw")


def test_corrupt_entry_is_a_miss(tmp_path) -> None:
    cache = Cache(tmp_path / "c")
    key = Cache.make_key("x")
    cache.put(key, {"ok": True})

    entry = cache.get(key)
    entry.path.write_text("{not json", encoding="utf-8")

    assert cache.get(key) is None


def test_force_refresh_bypasses_reads(tmp_path) -> None:
    cache = Cache(tmp_path / "c")
    key = Cache.make_key("x")
    cache.put(key, {"ok": True})

    fresh = Cache(tmp_path / "c", force_refresh=True)
    assert fresh.get(key) is None


def test_disabled_cache_writes_nothing(tmp_path) -> None:
    cache = Cache(tmp_path / "c", enabled=False)
    key = Cache.make_key("x")
    cache.put(key, {"ok": True})

    assert not (tmp_path / "c").exists()
    assert cache.get(key) is None


def test_binary_roundtrip(tmp_path) -> None:
    cache = Cache(tmp_path / "c")
    key = Cache.make_key("audio")

    cache.put_bytes(key, b"RIFFbits", {"kind": "wav"})

    assert cache.get_bytes(key) == b"RIFFbits"


def test_stats_count_hits_and_misses(tmp_path) -> None:
    cache = Cache(tmp_path / "c")
    key = Cache.make_key("x")
    cache.get(key)
    cache.put(key, {"ok": True})
    cache.get(key)

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
