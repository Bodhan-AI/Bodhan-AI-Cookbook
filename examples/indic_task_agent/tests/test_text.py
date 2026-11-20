from bodhan_mcp import text


def test_chunk_keeps_whole_sentences_under_limit():
    src = "पहला वाक्य है। दूसरा वाक्य है। तीसरा वाक्य है।"
    chunks = text.chunk_text(src, 25)
    assert all(len(c) <= 25 for c in chunks)
    assert " ".join(chunks) == src


def test_chunk_splits_only_overlong_sentence_at_a_space():
    long_sentence = " ".join(["word"] * 30)
    chunks = text.chunk_text(long_sentence, 40)
    assert all(len(c) <= 40 for c in chunks)
    assert " ".join(chunks) == long_sentence


def test_normalize_digits_handles_indic_numerals():
    assert text.normalize_digits("२५ ৫ ௧ ౩") == "25 5 1 3"


def test_missing_numbers_is_multiset_and_script_aware():
    assert text.missing_numbers("Read 25 pages and 3 chapters", "३ अध्याय और २५ पृष्ठ") == []
    assert text.missing_numbers("Read 25 pages and 3 chapters", "२५ पृष्ठ") == ["3"]


def test_markdown_table_structure_survives_translation():
    md = "# Title\n\n| Item | Qty |\n|---|---|\n| Pen | 4 |\n| Book | 12 |\n\n- first\n- second\n"
    seen = []

    def fake(fragment):
        seen.append(fragment)
        return f"<{fragment}>"

    out, stats = text.translate_markdown(md, fake)
    assert stats.tables == 1 and stats.table_rows == 4 and stats.headings == 1 and stats.list_items == 2
    assert "|---|---|" in out
    assert "| 4 |" in out and "| 12 |" in out  # numbers-only cells are not sent to the translator
    assert "4" not in seen and "12" not in seen
    assert text.structure_mismatches(text.structure_of(md), text.structure_of(out)) == []


def test_code_and_latex_blocks_are_not_translated():
    md = "Intro text here.\n\n```python\nprint('hello world')\n```\n\n$$\nx^2 + y^2 = z^2\n$$\n"
    seen = []
    out, stats = text.translate_markdown(md, lambda s: seen.append(s) or s.upper())
    assert "print('hello world')" in out
    assert "x^2 + y^2 = z^2" in out
    assert stats.fences == 1 and stats.math_blocks == 1
    assert seen == ["Intro text here."]


def test_detect_script_narrows_language_and_flags_shared_scripts():
    dev = text.detect_script("नमस्ते, आप कैसे हैं?")
    assert dev["dominant_script"] == "devanagari" and "hi" in dev["candidate_languages"] and dev["note"]
    tam = text.detect_script("வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்?")
    assert tam["candidate_languages"] == ["ta"]
    eng = text.detect_script("Hello there")
    assert eng["candidate_languages"] == ["en"]


def test_similarity_ignores_punctuation_and_digit_script():
    assert text.similarity("कक्षा ९ बजे शुरू होती है।", "कक्षा 9 बजे शुरू होती है") > 0.99
    assert text.similarity("कक्षा नौ बजे", "पूरी तरह अलग वाक्य") < 0.5
