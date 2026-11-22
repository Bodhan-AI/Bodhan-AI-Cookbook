from bodhan_mcp import textutil as tx


def test_chunk_text_never_splits_sentences_and_is_lossless():
    text = "First sentence here. Second one is a bit longer than the first! Third? " * 20
    chunks = tx.chunk_text(text, 120)
    assert all(len(c) <= 120 for c in chunks)
    assert all(c.rstrip()[-1] in ".!?" for c in chunks)  # every chunk ends on a sentence boundary
    assert " ".join(chunks).split() == text.split()


def test_chunk_text_handles_devanagari_danda():
    text = "यह पहला वाक्य है। यह दूसरा वाक्य है। यह तीसरा है।"
    assert tx.split_sentences(text) == ["यह पहला वाक्य है।", "यह दूसरा वाक्य है।", "यह तीसरा है।"]


def test_oversized_sentence_is_split_not_dropped():
    s = "word " * 100
    chunks = tx.chunk_text(s, 50)
    assert all(len(c) <= 50 for c in chunks)
    assert " ".join(chunks).split() == s.split()


def test_chunk_paragraphs_keeps_tables_whole():
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| {i} | {i * 2} |" for i in range(30))
    text = "Intro paragraph.\n\n" + table + "\n\nOutro."
    chunks = tx.chunk_paragraphs(text, 200)
    assert any(table in c for c in chunks)


def test_digits_normalise_across_scripts():
    assert tx.to_ascii_digits("२५ ও ৪২ ௧௨") == "25 ও 42 12"
    assert tx.extract_numbers("तापमान २५.५ डिग्री और 1,000") == ["25.5", "1000"]


def test_cer_ignores_punctuation_case_and_digit_script():
    assert tx.cer("Hello, World 25!", "hello world २५") == 0.0
    assert tx.cer("abcd", "abxd") == 0.25
    assert tx.cer("", "") == 0.0


def test_script_detection():
    assert tx.guess_language("வணக்கம் உலகம்")["language"] == "ta"
    assert tx.guess_language("The quick brown fox")["language"] == "en"
    g = tx.guess_language("नमस्ते दुनिया")
    assert g["language"] == "hi" and "mr" in g["candidates"] and g["confidence"] < 1.0
    assert tx.guess_language("1234 !!")["language"] is None


def test_structure_counts():
    c = tx.structure_counts("# Title\n\n- a\n- b\n\n| x | y |\n|---|---|\n\n$$a^2$$ and $b$")
    assert c["headings"] == 1 and c["list_items"] == 2 and c["table_rows"] == 2 and c["latex"] == 2
