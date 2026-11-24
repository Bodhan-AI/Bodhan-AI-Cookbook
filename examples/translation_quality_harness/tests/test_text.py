"""Normalization, span mapping, segmentation, numbers, alignment and speakable text."""

from __future__ import annotations

import unicodedata

from bodhan_eval.text.align import align_words, levenshtein, similarity
from bodhan_eval.text.normalize import fold_digits, normalize
from bodhan_eval.text.numbers import canonical, extract_numbers, scaled_match
from bodhan_eval.text.segment import clauses, sentences, stem, words
from bodhan_eval.text.speakable import chunk_for_tts, to_speakable


def test_native_digits_fold_to_ascii_in_every_script():
    assert normalize("२५ ২৫ ௨௫ ൨൫ ۲۵").text == "25 25 25 25 25"
    assert fold_digits("१०० km") == "100 km"


def test_zero_width_and_punctuation_are_normalized():
    n = normalize("नैदानिक ​​और “वैज्ञानिक” — विभाग।")
    assert "​" not in n.text
    assert n.text.endswith(".")
    assert '"' in n.text and "-" in n.text


def test_whitespace_collapses_and_trailing_space_is_dropped():
    assert normalize("  a \n\t b  ").text == "a b"


def test_span_maps_back_to_original_through_removed_characters():
    original = "क​ख २५ ग"
    n = normalize(original)
    i = n.text.index("25")
    s, e = n.span_to_orig(i, i + 2)
    assert original[s:e] == "२५"


def test_span_end_keeps_trailing_vowel_signs():
    original = "हिंदी भाषा"
    n = normalize(original)
    s, e = n.span_to_orig(0, n.text.index(" "))
    assert original[s:e] == "हिंदी"


def test_nfc_composes_split_bengali_vowel():
    decomposed = "কো"  # e-kar + aa-kar == o-kar
    assert normalize(decomposed).text == unicodedata.normalize("NFC", decomposed)
    assert len(normalize(decomposed).text) == 2


def test_asr_normalization_strips_nukta_and_punctuation():
    n = normalize("ज़रूर, हाँ!", strip_nukta=True, drop_punct=True)
    assert "़" not in n.text and "," not in n.text and "!" not in n.text
    assert "ं" in n.text  # candrabindu folded to anusvara


def test_sentences_respect_abbreviations_decimals_and_danda():
    text = "Dr. Rao measured 1.5 kg. It rained. वह आया। फिर गया।"
    got = [s.text for s in sentences(text)]
    assert got == ["Dr. Rao measured 1.5 kg.", "It rained.", "वह आया।", "फिर गया।"]
    for s in sentences(text):
        assert text[s.start : s.end] == s.text


def test_clauses_split_on_commas():
    assert [c.text for c in clauses("One, two three; four.")] == ["One,", "two three;", "four."]


def test_words_keep_indic_combining_marks():
    assert [w.text for w in words("हिंदी भाषा, தமிழ்!")] == ["हिंदी", "भाषा", "தமிழ்"]


def test_stem_matches_simple_inflections():
    assert stem("cells") == stem("cell")
    assert stem("studies") == "study"


def test_number_extraction_handles_grouping_decimals_and_native_digits():
    values = [m.value for m in extract_numbers("1,00,000 लोग, 2.50 किलो, १२ बजे, 3,4")]
    assert values == ["100000", "2.5", "12", "3", "4"]
    m = extract_numbers("कीमत ₹१,२०० है")[0]
    assert m.value == "1200" and "कीमत ₹१,२०० है"[m.start : m.end] == "१,२००"


def test_canonical_and_scaled_match():
    assert canonical("007") == "7"
    assert canonical("10.0") == "10"
    assert scaled_match("3", "30") and not scaled_match("3", "4")


def test_levenshtein_and_similarity():
    assert levenshtein("kitten", "sitting") == 3
    assert similarity("abc", "abc") == 1.0


def test_align_words_marks_deletions_and_wildcards():
    ops = align_words(["a", "b", "25", "c", "d"], ["a", "x", "c"], wildcard=lambda w: w.isdigit())
    kinds = [o.kind for o in ops]
    assert kinds.count("del") == 2
    assert ops[2].kind == "eq" or any(o.kind == "eq" and o.i == 2 for o in ops)


def test_speakable_strips_markup_and_maps_offsets():
    text = "## शीर्षक\n\n| # | पाठ |\n|---|---|\n| 1 | **पहला** |\n\nसूत्र $x^2$ देखें।"
    sp = to_speakable(text)
    assert "#" not in sp.text and "|" not in sp.text and "**" not in sp.text and "x^2" not in sp.text
    i = sp.text.index("पहला")
    s, e = sp.span_to_translation(i, i + len("पहला"))
    assert text[s:e] == "पहला"


def test_chunk_for_tts_respects_length():
    sp = to_speakable(" ".join(["यह एक लंबा वाक्य है।"] * 40))
    chunks = chunk_for_tts(sp, max_chars=120)
    assert chunks and all(e - s <= 120 for s, e in chunks)
    assert chunks[0][0] == 0 and chunks[-1][1] == len(sp.text)
