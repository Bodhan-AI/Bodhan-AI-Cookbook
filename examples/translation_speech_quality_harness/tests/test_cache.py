from pathlib import Path

from bodhan_eval.cache import Cache, make_key


def test_cache_hit_and_corrupt_entry(tmp_path: Path):
    cache = Cache(tmp_path)
    key = make_key("translate", "indic-translate", {"target_language": "hi"}, b"Hello")
    cache.put(key, {"text": "नमस्ते", "truncated": False})
    assert cache.get(key) == {"text": "नमस्ते", "truncated": False}

    entry = cache._entry_path(key)
    entry.write_bytes(b"not-json")
    assert cache.get(key) is None


def test_make_key_stable_across_parameter_order():
    a = make_key("/translate", "indic-translate", {"target_language": "hi", "script": "native"}, b"Hello")
    b = make_key("/translate", "indic-translate", {"script": "native", "target_language": "hi"}, b"Hello")
    assert a == b
    assert len(a) == 64
