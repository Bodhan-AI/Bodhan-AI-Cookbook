import pytest

from bodhan_core.chunking import split_text


def test_short_text_is_one_chunk():
    assert split_text("Good morning.", 1500) == ["Good morning."]


@pytest.mark.parametrize(
    "text",
    [
        "One. Two. Three. Four. Five. Six.",
        "Para one line.\n\nPara two line.\nSecond line here.\n\n\nPara three.",
        "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n",
        "यह पहला वाक्य है। यह दूसरा वाक्य है। यह तीसरा है।",
        "word " * 500,
        "x" * 1000,  # one unbreakable "word"
        "  leading and trailing  \n",
    ],
)
@pytest.mark.parametrize("limit", [1, 7, 20, 100])
def test_lossless_and_within_limit(text, limit):
    chunks = split_text(text, limit)
    assert "".join(chunks) == text
    assert all(len(c) <= limit for c in chunks)


def test_does_not_cut_sentences_when_they_fit():
    text = "First sentence here. Second sentence here. Third sentence here."
    chunks = split_text(text, 45)
    assert len(chunks) == 2
    assert all(c.rstrip().endswith(".") for c in chunks)


def test_splits_on_hindi_danda():
    text = "यह पहला वाक्य है। यह दूसरा वाक्य है।"
    assert len(split_text(text, 25)) == 2


def test_keeps_lines_together_when_they_fit():
    text = "line one\nline two\nline three\n"
    chunks = split_text(text, 18)
    assert chunks == ["line one\nline two\n", "line three\n"]


def test_invalid_limit():
    with pytest.raises(ValueError):
        split_text("abc", 0)
