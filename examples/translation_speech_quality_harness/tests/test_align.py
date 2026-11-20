from bodhan_eval.align import (
    align,
    align_tokens,
    counts,
    error_rate,
    extra_runs,
    missing_runs,
    repeated_ngrams,
    tokens_to_span,
)
from bodhan_eval.normalize import tokenize


def test_identical_alignment():
    ops = align(list("abc"), list("abc"))
    assert counts(ops) == {"equal": 3, "sub": 0, "del": 0, "ins": 0}
    assert error_rate(list("abc"), list("abc")) == 0.0


def test_deletion_run_is_localized():
    ref = "a b c d e f".split()
    hyp = "a b f".split()
    ops = align(ref, hyp)
    assert missing_runs(ops) == [(2, 5)]
    assert counts(ops)["del"] == 3


def test_insertion_run():
    ref = "a b c".split()
    hyp = "a b x y c".split()
    assert extra_runs(align(ref, hyp)) == [(2, 4)]


def test_missing_run_maps_to_char_span():
    text = "पानी समुद्र तल पर 100 डिग्री पर उबलता है"
    ref = tokenize(text)
    hyp = tokenize("पानी समुद्र तल पर उबलता है")  # dropped "100 डिग्री पर"
    runs = missing_runs(align_tokens(ref, hyp))
    assert len(runs) == 1
    s, e = tokens_to_span(ref, *runs[0])
    assert text[s:e] == "100 डिग्री पर"


def test_indic_digit_transcript_matches_ascii_text():
    ref = tokenize("तापमान 25 डिग्री")
    hyp = tokenize("तापमान २५ डिग्री")
    assert counts(align_tokens(ref, hyp))["equal"] == 3


def test_repeated_ngrams():
    seq = "the cat sat sat on the mat on the mat".split()
    reps = repeated_ngrams(seq, min_n=1)
    assert (2, 1, 2) in reps
    assert (4, 3, 2) in reps


def test_repeated_ngrams_none():
    assert repeated_ngrams("a b c d".split()) == []
