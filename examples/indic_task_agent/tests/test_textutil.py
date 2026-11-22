import pytest

from bodhan_mcp.textutil import chunk_text, split_sentences


def test_split_on_danda_and_periods():
    text = "यह पहला वाक्य है। यह दूसरा है! क्या यह तीसरा है? Done. Next one."
    assert split_sentences(text) == [
        "यह पहला वाक्य है।", "यह दूसरा है!", "क्या यह तीसरा है?", "Done.", "Next one.",
    ]


def test_decimals_and_runs_of_punctuation_not_split():
    assert split_sentences("It costs 3.5 rupees.") == ["It costs 3.5 rupees."]
    assert split_sentences("Really?! Yes.") == ["Really?!", "Yes."]


def test_newlines_split():
    assert split_sentences("line one\nline two") == ["line one", "line two"]


def test_chunks_respect_limit_and_lose_nothing():
    text = " ".join(f"This is sentence number {i} of the test." for i in range(30))
    chunks = chunk_text(text, max_chars=100)
    assert all(0 < len(c) <= 100 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_long_sentence_is_split_at_commas_or_spaces():
    sentence = "word, " * 100
    chunks = chunk_text(sentence, max_chars=50)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)
    assert " ".join(chunks).replace(",", "").split() == sentence.replace(",", "").split()


def test_unbreakable_token_is_hard_cut():
    chunks = chunk_text("a" * 130, max_chars=50)
    assert [len(c) for c in chunks] == [50, 50, 30]


def test_small_text_is_one_chunk():
    assert chunk_text("Hello there. How are you?", max_chars=200) == ["Hello there. How are you?"]


def test_empty_text_and_bad_limit():
    assert chunk_text("   ") == []
    with pytest.raises(ValueError):
        chunk_text("hi", max_chars=5)
