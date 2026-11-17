from bodhan_eval.normalize import cer, normalize, normalize_with_map, numbers, script_of


def test_native_digits_become_ascii():
    assert normalize("२५ किलो") == "25 किलो"
    assert normalize("১০০") == "100"
    assert normalize("௧௦") == "10"


def test_punctuation_case_and_zero_width_removed():
    assert normalize("Hello,  WORLD!") == "hello world"
    assert normalize("क्‍ष") == "क्ष"
    assert normalize("पानी। उबलता है॥") == "पानी उबलता है"


def test_spelling_variants_are_folded():
    assert normalize("ତତ୍ତ୍ୱ") == normalize("ତତ୍ତ୍ଵ")  # Odia wa / va (seen live in ASR output)
    assert normalize("ज़मीन") == normalize("जमीन")  # nukta
    assert normalize("माँ") == normalize("मां")  # candrabindu / anusvara


def test_offset_map_points_back_to_raw_text():
    raw = "A, b!  C"
    norm, offsets = normalize_with_map(raw)
    assert norm == "a b c"
    assert [raw[i] for i in offsets] == ["A", ",", "b", "!", "C"]  # a collapsed separator maps to its first raw char


def test_numbers_are_canonical_across_formats_and_scripts():
    assert [v for v, _, _ in numbers("1,00,000 and 100000")] == ["100000", "100000"]
    assert [v for v, _, _ in numbers("12.50 kg")] == ["12.5"]
    assert [v for v, _, _ in numbers("१९५० में")] == ["1950"]
    value, start, end = numbers("Cost is ₹700 today")[0]
    assert (value, start, end) == ("700", 9, 12)


def test_script_detection():
    assert script_of("क") == "DEVANAGARI"
    assert script_of("த") == "TAMIL"
    assert script_of("a") == "LATIN"
    assert script_of("1") is None


def test_cer_ignores_formatting_differences():
    assert cer("पानी 100 °C पर", "पानी १०० C पर") == 0.0
    assert 0 < cer("abcdef", "abcxef") < 0.2
    assert cer("", "") == 0.0
