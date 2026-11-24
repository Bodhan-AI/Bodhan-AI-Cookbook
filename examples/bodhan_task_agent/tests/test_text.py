"""Chunking and limit handling for TTS, translation, subtitles and verification metrics."""

import re

from bodhan_mcp import text


def _letters(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_tts_split_respects_limit_and_drops_nothing():
    src = ("यह पहला वाक्य है। " * 30) + "A very long clause, " * 40 + "end." + " x" * 5 + " " + "y" * 500
    pieces = text.split_for_tts(src, 220)
    assert all(len(p) <= 220 for p in pieces)
    assert _letters("".join(pieces)) == _letters(src)


def test_tts_split_packs_short_sentences():
    pieces = text.split_for_tts("One. Two. Three. Four.", 220)
    assert pieces == ["One. Two. Three. Four."]


def test_tts_split_on_danda_and_question_marks():
    pieces = text.split_for_tts("क्या हाल है? सब ठीक है। " * 20, 50)
    assert len(pieces) > 1 and all(len(p) <= 50 for p in pieces)


def test_translation_chunks_never_split_tables():
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| row {i} | {i} |" for i in range(200))
    md = ("Para one. " * 300) + "\n\n" + table + "\n\n" + ("Para two. " * 300)
    chunks = text.chunk_for_translation(md, 400)
    assert len(chunks) > 2
    holders = [c for c in chunks if "| row 0 |" in c]
    assert len(holders) == 1 and "| row 199 |" in holders[0]
    assert _letters("".join(chunks)) == _letters(md)


def test_translation_chunks_split_oversized_paragraph_at_sentences():
    para = " ".join(f"Sentence number {i} is here." for i in range(400))
    chunks = text.chunk_for_translation(para, 300)
    assert len(chunks) > 3
    assert all(c.rstrip().endswith(".") for c in chunks)


def test_split_tables_and_rebuild_roundtrip():
    md = "Intro text.\n\n| Name | Marks |\n|:---|---:|\n| Asha | 42 |\n| Ravi |  |\n\nOutro."
    parts = text.split_tables(md)
    assert [k for k, _ in parts] == ["prose", "table", "prose"]
    rows = text.parse_pipe_table(parts[1][1])
    assert rows[1] is None and rows[2] == ["Asha", "42"] and rows[3] == ["Ravi", ""]
    rebuilt = text.render_pipe_table(rows)
    assert text.table_shapes(rebuilt) == text.table_shapes(parts[1][1])


def test_numbers_normalise_across_scripts():
    assert text.numbers("कुल १,२०० रुपये") == text.numbers("total 1200 rupees")


def test_cer_and_repetition():
    assert text.cer("नमस्ते दुनिया", "नमस्ते दुनिया") == 0.0
    assert text.cer("abc", "xyz") == 1.0
    assert text.repeated_ngrams("a b c d a b c d a b c d", 4, 3)


def test_markdown_to_speech_reads_tables_as_lists():
    s = text.markdown_to_speech("# Title\n\n| Name | Marks |\n|---|---|\n| Asha | 42 |\n\n**Bold** [link](http://x)")
    assert "|" not in s and "#" not in s and "Asha, 42" in s and "link" in s and "http" not in s


def test_srt_formatting():
    srt = text.to_srt([(0.0, 1.5, "hello"), (3661.25, 3662.0, "later")])
    assert "00:00:00,000 --> 00:00:01,500" in srt and "01:01:01,250 --> 01:01:02,000" in srt
