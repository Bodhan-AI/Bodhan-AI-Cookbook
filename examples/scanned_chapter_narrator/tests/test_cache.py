"""Unit tests for DiskCache OCR and translation caching."""

from pathlib import Path

from bodhan_lesson.cache import DiskCache


def test_disk_cache_roundtrip(tmp_path: Path):
    cache = DiskCache(cache_dir=tmp_path / ".test_cache")

    # OCR caching
    img_data = b"fake_png_image_bytes_123"
    ocr_result = {"success": True, "markdown": "# Chapter 1"}
    assert cache.get_ocr(img_data) is None

    cache.set_ocr(img_data, ocr_result)
    retrieved = cache.get_ocr(img_data)
    assert retrieved is not None
    assert retrieved["markdown"] == "# Chapter 1"

    # Translation caching
    text = "Hello world"
    assert cache.get_translation(text, target_lang="hi", source_lang="en") is None
    cache.set_translation(text, target_lang="hi", translated_text="नमस्ते दुनिया", source_lang="en")
    cached_trans = cache.get_translation(text, target_lang="hi", source_lang="en")
    assert cached_trans == "नमस्ते दुनिया"
