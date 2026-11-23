import pytest

from app.utils.text import join_chunks, split_in_two, split_text


def test_roundtrip_preserves_all_text():
    text = ("నమస్కారం. మా గ్రామానికి స్వాగతం! " * 40).strip() + "\n\n" + "Second paragraph. It has two sentences."
    chunks = split_text(text, 120)
    assert len(chunks) > 3
    assert all(len(c.text) <= 120 for c in chunks)
    assert join_chunks(chunks) == text


def test_indic_danda_is_a_sentence_boundary():
    text = "यह पहला वाक्य है। यह दूसरा वाक्य है। यह तीसरा वाक्य है।"
    chunks = split_text(text, 25)
    assert [c.text for c in chunks][0].endswith("।")
    assert join_chunks(chunks) == text


def test_long_sentence_without_punctuation_is_still_split_losslessly():
    text = " ".join(f"word{i}" for i in range(200))
    chunks = split_text(text, 50)
    assert all(len(c.text) <= 50 for c in chunks)
    assert join_chunks(chunks) == text


def test_unbroken_string_hard_cut():
    text = "x" * 1000
    chunks = split_text(text, 100)
    assert len(chunks) == 10 and join_chunks(chunks) == text


def test_paragraph_breaks_are_kept_as_separators():
    chunks = split_text("One.\n\nTwo.", 100)
    assert [c.text for c in chunks] == ["One.", "Two."]
    assert chunks[0].separator == "\n\n"


def test_empty_and_tiny_limits():
    assert split_text("   ", 100) == []
    with pytest.raises(ValueError):
        split_text("abc", 5)


def test_split_in_two():
    a, b = split_in_two("First sentence is here. Second sentence follows. Third one ends it all now.")
    assert a and b
    assert split_in_two("short") is None
