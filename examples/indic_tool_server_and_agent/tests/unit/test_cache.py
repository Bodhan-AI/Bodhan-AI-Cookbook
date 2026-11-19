"""Unit tests for content-hash-based disk cache."""
from __future__ import annotations

from pathlib import Path
import pytest

from bodhan_mcp.core.cache import cache_get, cache_set, _make_key


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache_dir = tmp_path / "bodhan_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BODHAN_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr("bodhan_mcp.core.cache._CACHE_DIR", cache_dir)
    return cache_dir


def test_cache_hit_same_input():
    """Identical model, params, and content produces a cache hit."""
    model = "indic-translate"
    params = {"target": "hi", "src": "en"}
    content = "Hello world"
    result = {"translated_text": "नमस्ते दुनिया"}

    # Initially cache miss
    assert cache_get(model, params, content) is None

    # Set cache
    cache_set(model, params, content, result)

    # Now cache hit
    hit = cache_get(model, params, content)
    assert hit == result


def test_cache_miss_different_input():
    """Different input content produces a cache miss."""
    model = "indic-translate"
    params = {"target": "hi"}
    cache_set(model, params, "text A", {"out": "A"})

    assert cache_get(model, params, "text B") is None


def test_cache_miss_different_model():
    """Different model name produces a cache miss."""
    params = {"target": "hi"}
    content = "shared content"
    cache_set("model-A", params, content, {"data": 1})

    assert cache_get("model-B", params, content) is None


def test_cache_miss_different_settings():
    """Different parameters (e.g. target language) produce a cache miss."""
    model = "indic-translate"
    content = "Good morning"
    cache_set(model, {"target": "hi"}, content, {"out": "शुभ प्रभात"})

    assert cache_get(model, {"target": "ta"}, content) is None


def test_corrupt_cache_entry_ignored(isolated_cache_dir: Path):
    """Corrupted cache entry on disk is ignored and returns None."""
    model = "indic-ocr"
    params = {}
    content = b"sample-image-bytes"

    # Compute key and write corrupted non-JSON data directly to disk
    key = _make_key(model, params, content)
    cache_file = isolated_cache_dir / f"{key}.json"
    cache_file.write_text("NOT_VALID_JSON{:::}")

    # cache_get should safely catch error and return None
    assert cache_get(model, params, content) is None
