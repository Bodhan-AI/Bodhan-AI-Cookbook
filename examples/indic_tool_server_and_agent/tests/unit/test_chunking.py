"""Unit tests for text and sentence chunking."""
from __future__ import annotations

import pytest
from bodhan_mcp.core.chunking import chunk_text, chunk_text_for_tts, count_tokens


def test_chunk_text_small():
    text = "Hello world! This is a test."
    chunks = chunk_text(text, max_tokens=100)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_text_split_paragraphs():
    p1 = "Paragraph 1 with some text."
    p2 = "Paragraph 2 with different words."
    text = f"{p1}\n\n{p2}"
    chunks = chunk_text(text, max_tokens=5)
    assert len(chunks) >= 2


def test_chunk_text_for_tts_boundaries():
    text = "First sentence! Second sentence? Third sentence। Fourth sentence."
    chunks = chunk_text_for_tts(text, max_chars=30)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 35  # reasonable margin


def test_count_tokens():
    assert count_tokens("Hello world") > 0
    assert count_tokens("") >= 0
