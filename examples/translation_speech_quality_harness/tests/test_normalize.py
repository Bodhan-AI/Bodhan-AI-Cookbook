import unicodedata

import pytest

from bodhan_eval.normalize import extract_numbers, normalize


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("१ ٢ ३ ٤ ५ ६ ७ ٨ ٩ ०", "hi", "1 2 3 4 5 6 7 8 9 0"),
        ("1,00,000", "hi", "100000"),
        ("1,000,000", "en", "1000000"),
        ("दाम ३٫१४", "hi", "दाम 3.14"),
        ("فی ٣.١٤", "ur", "فی 3.14"),
    ],
)
def test_normalize(text, lang, expected):
    assert normalize(text, lang).text == expected


def test_nfc_not_nfkc():
    nukta = "\u0939\u093c"
    norm = normalize(nukta, "hi")
    assert norm.text == nukta
    assert len(norm.map) == len(norm.text)
    assert normalize("\uff21", "en").text == "\uff21"


@pytest.mark.parametrize(
    ("base", "chillu"),
    [
        ("\u0d23", "\u0d7a"),
        ("\u0d28", "\u0d7b"),
        ("\u0d30", "\u0d7c"),
        ("\u0d32", "\u0d7d"),
        ("\u0d33", "\u0d7e"),
        ("\u0d15", "\u0d7f"),
    ],
)
def test_malayalam_legacy_chillu_sequences(base, chillu):
    legacy = f"{base}\u0d4d\u200d"
    assert normalize(legacy, "ml").text == chillu
    assert normalize(chillu, "ml").text == chillu


@pytest.mark.parametrize(
    "precomposed",
    [*(chr(codepoint) for codepoint in range(0x0958, 0x0960)), "\u0929", "\u0931", "\u0934"],
)
def test_devanagari_nukta_forms_compare_equal(precomposed):
    decomposed = unicodedata.normalize("NFD", precomposed)
    assert normalize(precomposed, "hi").text == normalize(decomposed, "hi").text


def test_comparison_controls_and_unicode_spaces():
    controls = "\u200c\u200d\u200b\u2060\u00ad\ufeff\u061c\u200e\u200f\u202a\u202c\u2066\u2069"
    text = f"a{controls}b\u00a0c\u2009d"
    assert normalize(text, "en").text == "ab c d"


def test_urdu_letter_marks_and_digits():
    text = "\u064a\u0643\u0640\u064e\u065f\u0670 \u0661\u06f2"
    assert normalize(text, "ur").text == "\u06cc\u06a9 12"


@pytest.mark.parametrize(
    "zero",
    [
        0x0966,
        0x09E6,
        0x0A66,
        0x0AE6,
        0x0B66,
        0x0BE6,
        0x0C66,
        0x0CE6,
        0x0D66,
        0x0660,
        0x06F0,
    ],
)
def test_native_digit_systems_normalize_to_ascii(zero):
    digits = "".join(chr(zero + offset) for offset in range(10))
    assert normalize(digits, "hi").text == "0123456789"


def test_indian_grouping_decimals_and_script_punctuation():
    grouped = "\u0967\u0968,\u0969\u096a,\u096b\u096c\u096d.\u096e\u096f"
    punctuation = "\u0964\u0965\u06d4\u061f\u060c"
    assert normalize(grouped, "hi").text == "1234567.89"
    assert normalize(punctuation, "ur").text == "...?,"


def test_remove_format_marks_only_from_comparison():
    original = "a\u200db\u200c\uFEFFc\u200e"
    norm = normalize(original, "en")
    assert norm.text == "abc"
    assert original != norm.text


@pytest.mark.parametrize(
    ("text", "lang", "values"),
    [
        ("१,००,००० और ३.१४", "hi", ["100000", "3.14"]),
        ("1000 और ٢", "hi", ["1000", "2"]),
        ("1,000,000.25", "en", ["1000000.25"]),
    ],
)
def test_extract_numbers(text, lang, values):
    numbers = extract_numbers(text, lang)
    assert [str(number.value) for number in numbers] == values


def test_offset_map_round_trips_combining_marks():
    text = "कित्\u200dता १२"
    norm = normalize(text, "hi")
    for norm_index in range(len(norm.text)):
        original_index = norm.map[norm_index]
        assert 0 <= original_index < len(text)
