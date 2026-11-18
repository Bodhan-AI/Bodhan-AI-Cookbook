import pytest

from bodhan_core import chunking as ch


@pytest.mark.parametrize(
    "text,expected",
    [
        ("कल विद्यालय बंद रहेगा। परसों खुलेगा। ठीक है?", ["कल विद्यालय बंद रहेगा।", "परसों खुलेगा।", "ठीक है?"]),
        ("வணக்கம். இன்று வகுப்பு!", ["வணக்கம்.", "இன்று வகுப்பு!"]),
        ("یہ ایک جملہ ہے۔ دوسرا جملہ؟", ["یہ ایک جملہ ہے۔", "دوسرا جملہ؟"]),
        ("Line one\nLine two", ["Line one", "Line two"]),
        ('He said "Stop." Then left.', ['He said "Stop."', "Then left."]),
    ],
)
def test_split_sentences(text, expected):
    assert ch.split_sentences(text) == expected


def test_pack_sentences_respects_limit_and_keeps_every_word():
    text = " ".join(f"यह वाक्य संख्या {i} है।" for i in range(60))
    chunks = ch.pack_sentences(text, 120)
    assert all(len(c) <= 120 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_pack_sentences_splits_overlong_sentence_at_commas_then_spaces():
    long = "word " * 100 + "end, more words here, and the final part."
    chunks = ch.pack_sentences(long, 60)
    assert all(len(c) <= 60 for c in chunks)
    assert " ".join(chunks).split() == long.split()


def test_pack_sentences_handles_unbroken_text():
    chunks = ch.pack_sentences("x" * 250, 100)
    assert [len(c) for c in chunks] == [100, 100, 50]


def test_pack_paragraphs_groups_under_limit():
    paras = ["a" * 40, "b" * 40, "c" * 40, "d" * 200, "e" * 10]
    groups = ch.pack_paragraphs(paras, 100)
    assert groups == [[0, 1], [2], [3], [4]]


def test_stitch_overlap():
    assert ch.stitch_overlap("we will start the class", "the class today is maths") == "today is maths"
    assert ch.stitch_overlap("hello there", "general kenobi") == "general kenobi"
    assert ch.stitch_overlap("आज हम गणित", "गणित पढ़ेंगे") == "पढ़ेंगे"


def test_numbers_survive_digit_script_changes():
    assert ch.normalize_digits("४५ and ௫ and ৭") == "45 and 5 and 7"
    assert ch.missing_numbers("Maths 45, Science 38, call 011-2345", "गणित ४५, विज्ञान ३८, कॉल ०११-२३४५") == []
    assert ch.missing_numbers("Pay 120 by 5 June", "जून तक भुगतान करें") == ["120", "5"]


WORKSHEET = """# Worksheet 3

Answer all questions.

| Subject | Marks | Notes |
|---|:---:|---|
| Maths | 45 | Good work |
| Science | 38 | https://example.org |

- First item
- [ ] Second item
1. Numbered

```python
print("keep me")
```

<!-- page 2 -->
> A quote
---
Last line
wrapped here."""


def test_markdown_round_trip_is_lossless():
    assert ch.render_markdown(ch.parse_markdown(WORKSHEET)) == WORKSHEET.replace(
        "| Subject | Marks | Notes |\n|---|:---:|---|\n| Maths | 45 | Good work |\n| Science | 38 | https://example.org |",
        "| Subject | Marks | Notes |\n|---|:---:|---|\n| Maths | 45 | Good work |\n| Science | 38 | https://example.org |",
    ).replace("Last line\nwrapped here.", "Last line wrapped here.")


def test_markdown_blocks_and_translatable_cells():
    blocks = ch.parse_markdown(WORKSHEET)
    kinds = [b.kind for b in blocks if b.kind != "blank"]
    assert kinds == ["heading", "paragraph", "table", "list", "list", "list", "code", "comment", "quote", "hr", "paragraph"]
    table = next(b for b in blocks if b.kind == "table")
    assert table.rows == [["Subject", "Marks", "Notes"], ["Maths", "45", "Good work"], ["Science", "38", "https://example.org"]]
    cells = [c for row in table.rows for c in row if ch.is_translatable_cell(c)]
    assert cells == ["Subject", "Marks", "Notes", "Maths", "Good work", "Science"]
    assert next(b for b in blocks if b.kind == "list" and "Second" in b.text).prefix == "- [ ] "


def test_table_survives_cellwise_translation():
    blocks = ch.parse_markdown(WORKSHEET)
    for b in blocks:
        if b.kind == "table":
            b.rows = [[f"T({c})" if ch.is_translatable_cell(c) else c for c in row] for row in b.rows]
    out = ch.render_markdown(blocks)
    assert ch.table_shape(out) == ch.table_shape(WORKSHEET) == [(3, 3)]
    assert "|---|:---:|---|" in out and "| T(Maths) | 45 | T(Good work) |" in out


def test_ragged_table_is_padded():
    md = "| a | b | c |\n|---|---|---|\n| 1 |"
    assert ch.table_shape(ch.render_markdown(ch.parse_markdown(md))) == [(2, 3)]
