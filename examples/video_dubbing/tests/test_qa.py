from __future__ import annotations

from bodhan_dub import qa


def test_a_faithful_clip_passes() -> None:
    verdict = qa.evaluate("कोण नब्बे डिग्री है", "कोण नब्बे डिग्री है")

    assert verdict.verdict == "pass"
    assert verdict.cer == 0.0
    assert verdict.reason is None


def test_a_looped_phrase_is_caught() -> None:
    expected = "यह कोण नब्बे डिग्री का है"
    heard = "यह कोण नब्बे डिग्री का है " * 4
    verdict = qa.evaluate(expected, heard)

    assert verdict.repetition is True
    assert verdict.verdict == "fail"
    assert "repeated" in (verdict.reason or "")


def test_a_truncated_clip_is_caught() -> None:
    expected = "यह एक बहुत लंबा वाक्य है जिसमें कई शब्द हैं और अंत तक बोला जाना चाहिए"
    heard = "यह एक"
    verdict = qa.evaluate(expected, heard)

    assert verdict.truncation < 0.6
    assert verdict.verdict == "fail"
    assert "truncated" in (verdict.reason or "")


def test_empty_transcription_fails_clearly() -> None:
    verdict = qa.evaluate("कुछ भी", "")

    assert verdict.verdict == "fail"
    assert verdict.cer == 1.0
    assert "empty" in (verdict.reason or "")


def test_punctuation_only_transcription_fails() -> None:
    assert qa.evaluate("कुछ भी", "...").verdict == "fail"


def test_digit_script_differences_do_not_count_as_errors() -> None:
    verdict = qa.evaluate("कोण २५ डिग्री है", "कोण 25 डिग्री है")

    assert verdict.cer == 0.0
    assert verdict.verdict == "pass"


def test_a_single_wrong_word_is_tolerated() -> None:
    expected = "the angle is ninety degrees exactly"
    heard = "the angle is ninety degree exactly"
    verdict = qa.evaluate(expected, heard)

    assert verdict.cer < 0.25
    assert verdict.verdict == "pass"


def test_qa_short_clips_are_not_failed_on_cer() -> None:
    # A handful of characters: one wrong glyph moves CER a long way, so CER is
    # not trusted below SHORT_TEXT_CHARS.
    verdict = qa.evaluate("आज का दिन", "आज का दिम")

    assert verdict.cer > 0
    assert verdict.verdict == "pass"


def test_qa_short_clips_still_fail_on_truncation() -> None:
    verdict = qa.evaluate("नब्बे डिग्री", "न")

    assert verdict.verdict == "fail"


def test_qa_long_clips_still_use_cer() -> None:
    expected = "एक न्यून कोण नब्बे डिग्री से कम होता है और यह स्पष्ट है"
    heard = "एक न्यून कोण पैंतीस डिग्री से कम होता है और यह बहुत अलग है"
    verdict = qa.evaluate(expected, heard)

    assert verdict.verdict == "fail"


def test_report_shape() -> None:
    verdict = qa.evaluate("कुछ", "कुछ")
    payload = verdict.to_dict()

    assert set(payload) == {"cer", "repetition", "truncation", "verdict", "reason"}
