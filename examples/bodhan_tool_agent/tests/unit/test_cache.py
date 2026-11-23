"""Unit tests for SHA-256 result caching and deduplication."""

from __future__ import annotations

from pathlib import Path
from bodhan_agent.cache import ResultCache


def test_cache_hit_and_miss(tmp_path: Path):
    cache = ResultCache(cache_dir=tmp_path)

    tool = "translate_text"
    args = {"text": "Hello", "target_language": "hi"}

    assert cache.get(tool, args) is None

    result = {"success": True, "translation": "नमस्ते", "estimated_cost_inr": 0.05}
    cache.set(tool, args, result)

    cached = cache.get(tool, args)
    assert cached is not None
    assert cached["translation"] == "नमस्ते"


def test_cache_file_hash_normalization(tmp_path: Path):
    cache = ResultCache(cache_dir=tmp_path)
    file1 = tmp_path / "audio1.wav"
    file2 = tmp_path / "audio2.wav"

    content = b"IDENTICAL_AUDIO_DATA"
    file1.write_bytes(content)
    file2.write_bytes(content)

    key1 = cache.compute_key("transcribe_audio", {"file_path": str(file1)})
    key2 = cache.compute_key("transcribe_audio", {"file_path": str(file2)})

    assert key1 == key2
