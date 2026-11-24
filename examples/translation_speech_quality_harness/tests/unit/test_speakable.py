"""What the speech checks expect to hear, and what ``--generate`` sends to Indic-Speak (``checks.speech.speakable``)."""

from __future__ import annotations

from bodhan_eval.checks.speech import speakable, speakable_for_tts


def test_offsets_are_kept() -> None:
    text = "## शीर्षक\n- **पानी** $x^2$ है"
    assert len(speakable(text)) == len(text)


def test_table_divider_row_is_not_spoken() -> None:
    # seen live: the divider reached Indic-Speak as text and came back as invented words
    table = "| नाम | प्रतीक |\n|---|---|\n| मिनट | min |\n| :--- | ---: |"
    assert "-" not in speakable(table) and ":" not in speakable(table)
    assert speakable_for_tts(table) == "नाम प्रतीक मिनट min"


def test_markup_is_blanked_but_words_stay() -> None:
    assert speakable_for_tts("## शीर्षक\n1. **पानी** [लिंक](https://x.org) देखें") == "शीर्षक पानी लिंक देखें"
