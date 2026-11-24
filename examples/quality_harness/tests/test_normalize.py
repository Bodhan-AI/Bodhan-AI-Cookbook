from bodhan_eval.normalize import norm_for_asr, norm_for_compare, normalize


def test_indic_digits_map_to_ascii():
    assert norm_for_compare("२५") == "25"
    assert norm_for_compare("১০০") == "100"  # bengali
    assert norm_for_compare("௫") == "5"       # tamil


def test_offset_map_round_trip():
    r = normalize("तापमान २५ डिग्री")
    i = r.text.index("25")
    span = r.to_original_span((i, i + 2))
    assert r.original[span[0]:span[1]] == "२५"


def test_offset_map_with_whitespace_collapse():
    raw = "a    b"
    r = normalize(raw)
    assert r.text == "a b"
    # map the 'b' back
    j = r.text.index("b")
    s = r.to_original_span((j, j + 1))
    assert raw[s[0]:s[1]] == "b"


def test_punctuation_unification():
    assert norm_for_compare("पानी।") == "पानी."
    assert norm_for_compare("“hi”") == '"hi"'


def test_zero_width_stripped_for_compare():
    assert norm_for_compare("a‍b") == "ab"


def test_nfc_equivalence():
    nfd = "é"   # e + combining acute
    nfc = "é"    # é
    assert norm_for_compare(nfd) == norm_for_compare(nfc)


def test_asr_form_lowercases_and_strips_punct():
    assert norm_for_asr("Hello, WORLD!!") == "hello world"
