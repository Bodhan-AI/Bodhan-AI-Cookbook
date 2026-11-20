from bodhan_dub.text import chunks, compare, digits, native_digits, numbers, sentences


def test_numbers_reads_any_script():
    assert numbers("180 और ३६० डिग्री, ৪ ধরনের") == [4, 180, 360]


def test_digits_turns_small_number_words_into_digits():
    assert numbers(digits("Name one pair, Two angles")) == [1, 2]


def test_native_digits_only_changes_digits():
    assert native_digits("कोण 180 है, 3.5", "hi") == "कोण १८० है, ३.५"
    assert native_digits("180 டிகிரி", "ta") == "௧௮௦ டிகிரி"
    assert native_digits("180", "xx") == "180"


def test_sentences_split_on_danda_and_full_stop():
    assert sentences("एक। दो? Three. Four") == ["एक।", "दो?", "Three.", "Four"]


def test_chunks_keep_whole_sentences_under_the_limit():
    text = "A short one. " * 20
    parts = chunks(text, 60)
    assert all(len(p) < 60 for p in parts)
    assert " ".join(parts) == text.strip()


def test_compare_passes_spoken_numbers_and_labels():
    c = compare("कोण AOC 180 है", "कोण ए ओ सी एक सौ अस्सी है")
    assert c["missing"] == 0 and c["extra"] < 1.5


def test_compare_catches_omission():
    c = compare("दो कोण आसन्न होते हैं जब उनका शीर्ष समान होता है", "दो कोण आसन्न होते हैं")
    assert c["missing"] > 0.25


def test_compare_catches_repetition():
    c = compare("दो कोण आसन्न होते हैं", "दो कोण आसन्न होते हैं दो कोण आसन्न होते हैं दो कोण")
    assert c["extra"] > 1.5


def test_english_speech_is_compared_on_latin_letters():
    assert compare("The angle is right.", "the angle is right")["cer"] == 0.0
    assert compare("The angle is right.", "the angle", latin=True)["missing"] > 0.25
    assert compare("The angle is right.", "the angle is right", latin=True)["cer"] == 0.0
