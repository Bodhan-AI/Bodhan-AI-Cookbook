from narrator.text_utils import chunk_sentence, split_paragraphs, split_sentences


def test_split_paragraphs_at_blank_lines():
    text = "First paragraph.\n\nSecond paragraph."
    assert split_paragraphs(text) == ["First paragraph.", "Second paragraph."]


def test_split_paragraphs_ignores_extra_blank_lines():
    assert split_paragraphs("A.\n\n\n\nB.") == ["A.", "B."]


def test_split_paragraphs_empty_text():
    assert split_paragraphs("   ") == []


def test_split_sentences_english():
    assert split_sentences("One. Two? Three!") == ["One.", "Two?", "Three!"]


def test_split_sentences_with_danda():
    text = "शुक्रवारी ग्रंथालय बंद राहील। कृपया पुस्तके परत करा।"
    assert split_sentences(text) == [
        "शुक्रवारी ग्रंथालय बंद राहील।",
        "कृपया पुस्तके परत करा।",
    ]


def test_split_sentences_without_punctuation_stays_whole():
    assert split_sentences("no punctuation here") == ["no punctuation here"]


def test_chunk_sentence_short_stays_whole():
    assert chunk_sentence("Short one.", limit=50) == ["Short one."]


def test_chunk_sentence_respects_limit():
    sentence = "word, " * 100
    for chunk in chunk_sentence(sentence, limit=40):
        assert len(chunk) <= 40


def test_chunk_sentence_cuts_a_huge_word():
    chunks = chunk_sentence("x" * 95, limit=40)
    assert all(len(c) <= 40 for c in chunks)
    assert "".join(chunks) == "x" * 95
    