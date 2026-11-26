from bodhan_eval.align import overlap, split_sentences
from bodhan_eval.normalize import ascii_digits, mask_markup, normalize_text, tokenize


def test_indic_digits_map_to_ascii_same_length():
    for s in ["२५", "২৫", "௨௫", "٢٥", "25"]:
        assert ascii_digits(s) == "25"
        assert len(ascii_digits(s)) == len(s)


def test_unicode_variants_normalize_equal():
    # Bengali YYA: precomposed U+09DF vs decomposed U+09AF U+09BC
    assert normalize_text("য়") == normalize_text("য়")
    # zero-width joiner noise is dropped
    assert normalize_text("क‍ष") == normalize_text("कष")


def test_tokenizer_keeps_combining_marks_and_offsets():
    text = "नमस्ते दुनिया, 25 बार।"
    toks = tokenize(text)
    assert [t.raw for t in toks] == ["नमस्ते", "दुनिया", "25", "बार"]
    for t in toks:
        assert text[t.start:t.end] == t.raw


def test_punctuation_danda_does_not_become_token():
    assert normalize_text("यह है।") == "यह है"


def test_split_sentences_handles_danda_decimals_newlines():
    t = "Pi is 3.14 here. Next one!\nLine two।"
    spans = split_sentences(t)
    assert [t[a:b] for a, b in spans] == ["Pi is 3.14 here.", "Next one!", "Line two।"]


def test_overlap():
    assert overlap([0, 5], [3, 9]) == 2
    assert overlap([0, 2], [2, 4]) == 0
    assert overlap(None, [1, 2]) == 0


def test_mask_markup_preserves_length():
    t = "see `code` and $x^2$ at https://a.b/c ok"
    m = mask_markup(t)
    assert len(m) == len(t) and "code" not in m and "x^2" not in m and "https" not in m
