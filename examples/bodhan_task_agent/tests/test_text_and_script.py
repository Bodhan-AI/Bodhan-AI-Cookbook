from __future__ import annotations

import pytest

from bodhan_mcp.utils.pricing import speak_cost, transcribe_cost, translate_cost
from bodhan_mcp.utils.script import dominant_script, language_from_script, script_counts
from bodhan_mcp.utils.text import (
    check_structure,
    check_tables,
    preview_text,
    restore_table_delimiters,
    split_for_speech,
    split_text,
)

HINDI = (
    "हर सुबह गाँव का बाज़ार सूर्योदय से पहले खुल जाता है। किसान ताज़ी सब्जियाँ, फल और फूल लेकर आते हैं, और हवा चाय "
    "और तले हुए स्नैक्स की गंध से भर जाती है। बच्चे अपने माता-पिता को स्टॉल लगाने में मदद करते हैं, जबकि बड़े लोग "
    "एक साथ बैठकर मौसम, फसल और शहर से आई खबरों के बारे में बात करते हैं।\n\nआठ बजे तक बाज़ार भरा होता है।"
)


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("limit", [250, 80, 30])
def test_split_for_speech_keeps_every_word_within_limit(limit: int) -> None:
    pieces = split_for_speech(HINDI, limit)
    assert all(len(piece) <= limit for piece in pieces)
    assert _flat(" ".join(pieces)) == _flat(HINDI)


def test_split_for_speech_prefers_sentence_then_clause_boundaries() -> None:
    assert split_for_speech(HINDI, 250)[0].endswith("।")
    assert all(piece.endswith(("।", ",")) for piece in split_for_speech(HINDI, 80)[:-1])


def test_split_for_speech_edge_cases() -> None:
    assert split_for_speech("  \n\n ", 250) == []
    assert len(split_for_speech("a" * 600, 250)) == 3
    with pytest.raises(ValueError):
        split_for_speech("text", 0)


def test_split_text_keeps_tables_whole_and_text_intact() -> None:
    table = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    source = "Intro sentence. " * 20 + "\n\n" + table + "\nOutro."
    chunks = split_text(source, 200)
    assert "".join(chunks) == source
    assert any(table in chunk for chunk in chunks)


def test_check_tables_flags_shape_change() -> None:
    source = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    assert check_tables(source, source) == []
    assert check_tables(source, "| a |\n| --- |\n| 1 |\n")


def test_preview_text_cuts_at_whitespace() -> None:
    assert preview_text("short", 10) == "short"
    assert preview_text("one two three", 9) == "one two"
    assert preview_text("x" * 20, 5) == "x" * 5


@pytest.mark.parametrize(
    ("text", "script", "language", "candidates"),
    [
        ("ఇది తెలుగు", "Telugu", "te", []),
        ("இது தமிழ்", "Tamil", "ta", []),
        ("यह हिंदी है", "Devanagari", None, ["hi", "mr"]),
        ("زندگی میں", "Arabic", None, ["ur"]),
        ("hello world", "Latin", None, ["en"]),
        ("123 ... !!", None, None, []),
    ],
)
def test_script_to_language(text: str, script: str | None, language: str | None, candidates: list[str]) -> None:
    detected = dominant_script(text)
    assert detected == script
    found, options = language_from_script(detected)
    assert found == language
    assert set(candidates) <= set(options)


def test_script_counts_ignores_digits_and_punctuation() -> None:
    counts = script_counts("राम 2025, ok!")
    assert set(counts) == {"Devanagari", "Latin"}
    assert counts["Latin"] == 2


TABLE_SOURCE = (
    "# Title\n\nIntro.\n\n| Item | Qty | Price |\n|:---|:---:|---:|\n| a | 1 | 2 |\n\n"
    "- one\n- two\n\n### Reminders\n\nText.\n"
)
TABLE_OUTPUT = (
    "# शीर्षक\n\nपरिचय।\n\n| वस्तु | मात्रा | मूल्य |\n|---|---|---|\n| क | 1 | 2 |\n\n- एक\n- दो\n\nअनुस्मारक\n\nपाठ।\n"
)


def test_restore_table_delimiters_puts_back_alignment_only() -> None:
    fixed = restore_table_delimiters(TABLE_SOURCE, TABLE_OUTPUT)
    assert "|:---|:---:|---:|" in fixed
    assert fixed.replace("|:---|:---:|---:|", "|---|---|---|") == TABLE_OUTPUT
    narrower = TABLE_OUTPUT.replace("|---|---|---|", "|---|---|")
    assert restore_table_delimiters(TABLE_SOURCE, narrower) == narrower
    assert restore_table_delimiters("No tables here.", TABLE_OUTPUT) == TABLE_OUTPUT


def test_check_structure_flags_lost_headings_and_list_items() -> None:
    assert check_structure(TABLE_SOURCE, TABLE_SOURCE) == []
    warnings = check_structure(TABLE_SOURCE, TABLE_OUTPUT)
    assert len(warnings) == 1 and "2 Markdown heading(s)" in warnings[0]
    assert "list item" in check_structure(TABLE_SOURCE, TABLE_SOURCE.replace("- two\n", ""))[0]


def test_costs_are_rounded() -> None:
    assert transcribe_cost(31.3) == 0.052167
    assert speak_cost(33) == 0.0198
    assert translate_cost(None) is None
