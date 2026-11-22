"""Unit tests for the SQLite result cache."""
from __future__ import annotations

from pathlib import Path

import pytest

from bodhan_agent.cache import ResultCache


@pytest.fixture()
def cache(tmp_path: Path) -> ResultCache:
    return ResultCache(tmp_path / "cache.db")


def test_miss_returns_none(cache: ResultCache) -> None:
    assert cache.get("transcribe", {"audio": "x.wav"}) is None


def test_set_then_get_returns_value(cache: ResultCache) -> None:
    cache.set("transcribe", {"audio": "a.wav"}, {"text": "hello", "language": "hi"})
    result = cache.get("transcribe", {"audio": "a.wav"})
    assert result == {"text": "hello", "language": "hi"}


def test_different_model_different_key(cache: ResultCache) -> None:
    cache.set("translate", {"text": "hi"}, {"translated_text": "नमस्ते"})
    assert cache.get("transcribe", {"text": "hi"}) is None


def test_key_is_input_order_independent(cache: ResultCache) -> None:
    """Cache key must be stable regardless of dict key order."""
    cache.set("ocr", {"b": 2, "a": 1}, {"text": "result"})
    assert cache.get("ocr", {"a": 1, "b": 2}) == {"text": "result"}


def test_overwrite_replaces_value(cache: ResultCache) -> None:
    cache.set("ocr", {"f": "img.png"}, {"text": "old"})
    cache.set("ocr", {"f": "img.png"}, {"text": "new"})
    assert cache.get("ocr", {"f": "img.png"}) == {"text": "new"}
