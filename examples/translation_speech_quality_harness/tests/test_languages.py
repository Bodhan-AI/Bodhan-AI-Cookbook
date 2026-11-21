import pytest

from bodhan_eval.languages import dominant_script, get_lang


@pytest.mark.parametrize(
    ("code", "ranges", "digits_start"),
    [
        ("en", [(0x0000, 0x052F)], 0),
        ("hi", [(0x0900, 0x097F)], 0x0966),
        ("bn", [(0x0980, 0x09FF)], 0x09E6),
        ("mr", [(0x0900, 0x097F)], 0x0966),
        ("te", [(0x0C00, 0x0C7F)], 0x0C66),
        ("ta", [(0x0B80, 0x0BFF)], 0x0BE6),
        ("gu", [(0x0A80, 0x0AFF)], 0x0AE6),
        ("kn", [(0x0C80, 0x0CFF)], 0x0CE6),
        ("ml", [(0x0D00, 0x0D7F)], 0x0D66),
        ("or", [(0x0B00, 0x0B7F)], 0x0B66),
        ("pa", [(0x0A00, 0x0A7F)], 0x0A66),
        ("as", [(0x0980, 0x09FF)], 0x09E6),
        ("ur", [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)], 0x0660),
    ],
)
def test_all_languages_present(code, ranges, digits_start):
    info = get_lang(code)
    assert info is not None
    assert info.ranges == ranges
    assert info.digits_start == digits_start


def test_unknown_language_returns_none():
    assert get_lang("xx") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This is English", "Latin"),
        ("\u0939\u093f\u0902\u0926\u0940", "Devanagari"),
        ("\u0996\u09ac\u09b0", "Bengali"),
        ("\u0ba4\u0bae\u0bbf\u0bb4\u0bcd", "Tamil"),
        ("\u0627\u0631\u062f\u0648", "Arabic"),
    ],
)
def test_dominant_script(text, expected):
    assert dominant_script(text) == expected
