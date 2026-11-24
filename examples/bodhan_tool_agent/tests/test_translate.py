"""Unit tests for the translate text splitting logic."""
from __future__ import annotations

from bodhan_mcp.tools.translate import _split_at_paragraphs


def test_short_text_is_one_chunk() -> None:
    text = "Hello world."
    chunks = _split_at_paragraphs(text, max_chars=1000)
    assert chunks == [text]


def test_long_text_splits_at_paragraphs() -> None:
    para = "A" * 500
    text = f"{para}\n\n{para}\n\n{para}"
    chunks = _split_at_paragraphs(text, max_chars=600)
    assert len(chunks) == 3
    for chunk in chunks:
        assert len(chunk) <= 600


def test_blank_paragraphs_are_skipped() -> None:
    text = "First.\n\n\n\nSecond."
    chunks = _split_at_paragraphs(text, max_chars=1000)
    assert len(chunks) == 1
    assert "First" in chunks[0] and "Second" in chunks[0]


def test_oversized_single_paragraph_becomes_one_chunk() -> None:
    """A single paragraph larger than max_chars must still be returned (not dropped)."""
    big = "X" * 5000
    chunks = _split_at_paragraphs(big, max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0] == big


async def test_same_lang_returns_identity() -> None:
    """translate_text with same source and target returns the original text at zero cost."""
    from bodhan_mcp.tools.translate import translate_text

    result = await translate_text("hello", "hi", "hi")
    assert result["translated_text"] == "hello"
    assert result["cost_inr"] == 0.0
