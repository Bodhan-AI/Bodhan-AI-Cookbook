from bodhan_eval.checks.roundtrip import align, segment


def test_sentence_segment_and_monosplit_alignment():
    source = segment("The first sentence. The second sentence.", "en")
    target = segment("पहला वाक्य। दूसरा वाक्य।", "hi")
    alignment = align(source, target)
    assert len(alignment.pairs) >= 1


def test_split_and_merge_alignment_has_pairs():
    source = segment("One short sentence. Another.", "en")
    target = segment("एक छोटा वाक्य है और दूसरा है।", "hi")
    alignment = align(source, target)
    assert alignment.pairs
