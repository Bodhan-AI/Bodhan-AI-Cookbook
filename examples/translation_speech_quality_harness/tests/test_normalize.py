import unicodedata

from bodhan_eval.normalize import (
    dominant_script,
    extract_numbers,
    merge_spans,
    normalize_text,
    script_profile,
    spans_overlap,
    split_sentences,
    to_ascii_digits,
    tokenize,
)


def test_indic_digits_to_ascii_all_scripts():
    assert to_ascii_digits("२५") == "25"  # Devanagari
    assert to_ascii_digits("২৫") == "25"  # Bengali
    assert to_ascii_digits("௨௫") == "25"  # Tamil
    assert to_ascii_digits("۲۵") == "25"  # Extended Arabic-Indic (Urdu)
    assert to_ascii_digits("᱒᱕") == "25"  # Ol Chiki (Santali)


def test_ascii_digits_is_length_preserving():
    s = "पानी १०० °C पर"
    assert len(to_ascii_digits(s)) == len(s)


def test_normalize_handles_zero_width_and_danda_and_nfc():
    a = "क्‍ष।"  # KA VIRAMA ZWJ SSA DANDA
    b = "क्ष."
    assert normalize_text(a) == normalize_text(b)
    # precomposed QA (U+0958) vs KA + NUKTA are canonically equivalent
    assert normalize_text("क़") == normalize_text("क़")


def test_loose_folds_nukta_and_candrabindu():
    assert normalize_text("ज़रूर", loose=True) == normalize_text("जरूर", loose=True)
    assert normalize_text("हँसी", loose=True) == normalize_text("हंसी", loose=True)
    assert normalize_text("ज़रूर") != normalize_text("जरूर")


def test_tokenize_keeps_vowel_signs_and_offsets():
    text = "नमस्ते, दुनिया!"
    toks = tokenize(text)
    assert [t.text for t in toks] == ["नमस्ते", "दुनिया"]
    for t in toks:
        assert text[t.start : t.end] == t.text


def test_tokenize_keeps_numbers_whole():
    toks = tokenize("Cost: 1,00,000 and 3.5 kg.")
    assert [t.text for t in toks] == ["Cost", "1,00,000", "and", "3.5", "kg"]


def test_tokenize_norm_maps_indic_digits():
    toks = tokenize("तापमान १०० है")
    assert toks[1].norm == "100"


def test_script_profile_and_dominant():
    assert dominant_script("यह हिंदी है") == "Deva"
    assert dominant_script("এটি বাংলা") == "Beng"
    assert dominant_script("இது தமிழ்") == "Taml"
    assert dominant_script("123 !!") is None
    prof = script_profile("Hindi हिंदी")
    assert set(prof) == {"Latn", "Deva"}
    assert abs(sum(prof.values()) - 1) < 1e-9


def test_danda_and_digits_do_not_count_as_letters():
    assert script_profile("१२३।") == {}


def test_extract_numbers_canonical_and_spans():
    text = "Population ১,০০,০০০ grew 3.50% in 2024; ratio 2,3."
    nums = extract_numbers(text)
    values = [n.value for n in nums]
    assert values == ["100000", "3.5", "2024", "2", "3"]
    first = nums[0]
    assert text[first.start : first.end] == "১,০০,০০০"


def test_extract_numbers_western_grouping_and_leading_zero():
    assert [n.value for n in extract_numbers("1,234,567 and 007 and 0.0")] == ["1234567", "7", "0"]


def test_split_sentences_handles_danda_decimal_and_markdown():
    text = "पहला वाक्य। दूसरा 3.5 किलो है।\n\n| a | b |\n| 1 | 2 |\n- item"
    segs = [text[s:e] for s, e in split_sentences(text)]
    assert segs == ["पहला वाक्य।", "दूसरा 3.5 किलो है।", "| a | b |", "| 1 | 2 |", "- item"]


def test_spans():
    assert spans_overlap((0, 5), (4, 8))
    assert not spans_overlap((0, 5), (5, 8))
    assert merge_spans([(5, 8), (0, 3), (2, 6)]) == [(0, 8)]


def test_nfc_does_not_break_offsets():
    # Decomposed input: offsets refer to the original string, not the NFC form.
    text = unicodedata.normalize("NFD", "café 25")
    toks = tokenize(text)
    assert text[toks[0].start : toks[0].end] == toks[0].text
    assert toks[0].norm == "café"


def test_numbers_after_abbreviation_dot_and_not_glued_units():
    from bodhan_eval.normalize import extract_numbers

    assert [n.value for n in extract_numbers("National Waterway No.1")] == ["1"]
    assert [n.value for n in extract_numbers("310,000 km2 of CO2")] == ["310000"]
    assert [n.value for n in extract_numbers("3.5 and १२")] == ["3.5", "12"]
    assert [n.value for n in extract_numbers("2005இல்")] == ["2005"]
