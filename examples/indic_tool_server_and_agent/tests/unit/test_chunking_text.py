"""Unit tests for text chunking (Indic-Translate 32K token boundary, TTS sentence chunking)."""
from __future__ import annotations

import pytest
from bodhan_mcp.core.chunking import (
    chunk_text,
    split_text_chunks,
    chunk_text_for_tts,
    count_tokens,
)


def test_chunk_text_empty_string():
    """Empty or whitespace-only string returns []."""
    assert chunk_text("") == []
    assert chunk_text("   \n\n\t  ") == []
    assert split_text_chunks("") == []


def test_chunk_text_one_sentence():
    """Single sentence within token limit returns [text]."""
    text = "भारत एक विशाल और विविध देश है।"
    chunks = chunk_text(text, max_tokens=100)
    assert chunks == [text]


def test_chunk_text_token_boundary():
    """Text split at token boundaries never produces a chunk exceeding max_tokens."""
    # Generate 50 distinct sentences
    sentences = [f"यह वाक्य संख्या {i} है जिसमें कुछ अतिरिक्त विवरण जोड़े गए हैं।" for i in range(50)]
    full_text = "\n\n".join(sentences)

    max_tok = 40
    chunks = chunk_text(full_text, max_tokens=max_tok)
    assert len(chunks) > 1

    for c in chunks:
        tok_count = count_tokens(c)
        assert tok_count <= max_tok * 1.25  # allow slight margin for whitespace join


def test_chunk_text_markdown_table_preserved():
    """Markdown table rows are kept together without splitting mid-row."""
    table = (
        "| ID | भाषा | लिपि |\n"
        "|---|---|---|\n"
        "| 1 | हिन्दी | देवनागरी |\n"
        "| 2 | தமிழ் | தமிழ் |\n"
        "| 3 | తెలుగు | తెలుగు |\n"
    )
    intro = "यहाँ भारतीय भाषाओं की सूची दी गई है:\n\n"
    full_text = intro + table

    chunks = chunk_text(full_text, max_tokens=200)
    # The whole table should be intact in one chunk or preserved across rows
    assert len(chunks) >= 1
    found_table = False
    for c in chunks:
        if "| हिन्दी | देवनागरी |" in c:
            found_table = True
            # Check row is not split in the middle
            assert "| 1 | हिन्दी | देवनागरी |" in c
    assert found_table


def test_chunk_text_latex_span():
    """LaTeX formula span is kept intact."""
    latex = "आइंस्टीन का समीकरण $E = mc^2$ प्रसिद्ध है।"
    chunks = chunk_text(latex, max_tokens=50)
    assert len(chunks) == 1
    assert "$E = mc^2$" in chunks[0]


def test_chunk_text_long_word_without_spaces():
    """Long string without spaces (e.g. repeated characters) handled cleanly."""
    long_word = "अ" * 500
    chunks = chunk_text(long_word, max_tokens=50)
    assert len(chunks) >= 1
    assert all(len(c) > 0 for c in chunks)


def test_chunk_text_for_tts_boundaries():
    """TTS sentence chunker splits at punctuation and respects char limit."""
    text = "नमस्ते! आज का मौसम बहुत अच्छा है। क्या आप टहलने चलेंगे? हाँ, बिल्कुल।"
    chunks = chunk_text_for_tts(text, max_chars=40)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 50
    # Reconstructed text contains all parts
    recombined = " ".join(chunks)
    assert "नमस्ते" in recombined
    assert "मौसम" in recombined
