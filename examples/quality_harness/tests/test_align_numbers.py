from bodhan_eval.align import (
    overlap_fraction_of,
    overlap_iou,
    spans_overlap,
    split_sentences,
)
from bodhan_eval.numbers import extract_numbers, multiset_diff


def test_split_sentences_mixed_script():
    s = split_sentences("पानी उबलता है। यह गर्म है।")
    assert len(s) == 2
    assert s[0].text.strip().endswith("।")


def test_split_sentences_ascii():
    s = split_sentences("One. Two? Three!")
    assert len(s) == 3


def test_span_overlap_helpers():
    assert spans_overlap((0, 5), (3, 8))
    assert not spans_overlap((0, 5), (5, 8))
    assert overlap_iou((0, 10), (0, 10)) == 1.0
    assert overlap_fraction_of((2, 4), (0, 10)) == 1.0
    assert overlap_fraction_of((0, 10), (0, 5)) == 0.5


def test_extract_numbers_and_units():
    nums = extract_numbers("The dose is 100 mg and 2.5 L.", "en")
    vals = {n.value for n in nums}
    assert "100" in vals and "2.5" in vals


def test_number_multiset_diff_detects_change():
    src = extract_numbers("boils at 100 degrees", "en")
    tgt = extract_numbers("90 पर", "hi")
    missing, extra = multiset_diff(src, tgt)
    assert any(m.value == "100" for m in missing)
    assert any(e.value == "90" for e in extra)


def test_indic_digits_compared_equal():
    src = extract_numbers("value 25", "en")
    tgt = extract_numbers("मान २५", "hi")
    missing, extra = multiset_diff(src, tgt)
    assert not missing and not extra
