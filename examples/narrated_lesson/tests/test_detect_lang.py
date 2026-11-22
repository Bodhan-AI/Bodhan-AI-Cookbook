"""Unit tests for source-language detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from bodhan_lesson.detect_lang import (
    detect_source_lang,
    detect_source_lang_from_path,
    map_fasttext_to_source,
)


def test_map_fasttext_aliases() -> None:
    assert map_fasttext_to_source("gom") == "kok"
    assert map_fasttext_to_source("hi") == "hi"
    assert map_fasttext_to_source("en") == "en"
    assert map_fasttext_to_source("zz") is None


def test_detect_source_lang_uses_injectable_fn() -> None:
    text = "यह एक लंबा पर्याप्त हिंदी पाठ है जो स्रोत भाषा का पता लगाने के लिए इस्तेमाल होता है।"
    lang, score = detect_source_lang(
        text,
        detect_fn=lambda _t: {"lang": "hi", "score": 0.91},
    )
    assert lang == "hi"
    assert score == 0.91


def test_detect_source_lang_maps_gom_to_kok() -> None:
    text = "x" * 50
    lang, score = detect_source_lang(
        text,
        detect_fn=lambda _t: {"lang": "gom", "score": 0.8},
    )
    assert lang == "kok"
    assert score == 0.8


def test_detect_source_lang_rejects_short_text() -> None:
    with pytest.raises(ValueError, match="Not enough text"):
        detect_source_lang("hi", detect_fn=lambda _t: {"lang": "hi", "score": 1.0})


def test_detect_source_lang_rejects_unsupported() -> None:
    with pytest.raises(ValueError, match="not a supported"):
        detect_source_lang(
            "x" * 50,
            detect_fn=lambda _t: {"lang": "zz", "score": 0.9},
        )


def test_detect_source_lang_rejects_low_score() -> None:
    with pytest.raises(ValueError, match="Low-confidence"):
        detect_source_lang(
            "x" * 50,
            detect_fn=lambda _t: {"lang": "hi", "score": 0.05},
        )


def test_detect_source_lang_from_path(tmp_path: Path) -> None:
    path = tmp_path / "lesson.src.md"
    path.write_text(
        "This is a long enough English lesson sample for source detection.\n" * 3,
        encoding="utf-8",
    )
    lang, score = detect_source_lang_from_path(
        path,
        detect_fn=lambda _t: {"lang": "en", "score": 0.99},
    )
    assert lang == "en"
    assert score == 0.99
