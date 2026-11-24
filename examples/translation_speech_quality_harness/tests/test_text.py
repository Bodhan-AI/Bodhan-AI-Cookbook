"""Normalization, offset maps, tokenization, numbers and alignment."""
from bodhan_eval.text.align import align, error_rate, runs
from bodhan_eval.text.markup import speakable, structure
from bodhan_eval.text.normalize import normalize, sentences, tokenize
from bodhan_eval.text.numbers import canonical, english_number_values, extract_numbers


def test_indic_digits_map_to_ascii_in_every_script():
    assert normalize("२५", "strict").text == "25"  # Devanagari
    assert normalize("২৫", "strict").text == "25"  # Bengali
    assert normalize("௨௫", "strict").text == "25"  # Tamil
    assert normalize("٢٥", "strict").text == "25"  # Arabic-Indic


def test_offset_map_points_back_to_original():
    text = "पानी‌ १०० °C।"
    n = normalize(text, "strict")
    i = n.text.index("100")
    s, e = n.to_original(i, i + 3)
    assert text[s:e] == "१००"


def test_precomposed_nukta_equals_decomposed():
    assert normalize("क़", "strict").text == normalize("क़", "strict").text  # क़


def test_speech_level_drops_punctuation_nukta_and_candrabindu():
    assert normalize("क़लम, चाँद!", "speech").text == "कलम चांद"


def test_danda_and_quotes_unified():
    assert normalize("यह “ठीक” है।", "strict").text == 'यह "ठीक" है.'


def test_tokenize_keeps_combining_marks_and_offsets():
    text = "किताबें पढ़ो"
    toks = tokenize(text)
    assert [text[t.start:t.end] for t in toks] == ["किताबें", "पढ़ो"]


def test_sentences_split_on_danda_but_not_decimals():
    text = "पृथ्वी 365.25 दिन लेती है। यह सच है।"
    spans = sentences(text)
    assert [text[s:e] for s, e in spans] == ["पृथ्वी 365.25 दिन लेती है।", "यह सच है।"]


def test_number_canonical_forms():
    assert canonical("1,00,000") == canonical("100,000") == "100000"
    assert canonical("3.50") == "3.5"
    assert canonical("05") == "5"
    vals = [n.value for n in extract_numbers("১৯৪৭ সালের ১৫ই আগস্ট")]
    assert vals == ["1947", "15"]


def test_english_number_words():
    v = english_number_values("two hundred and fifty people, 15 dogs, the first day, a dozen eggs")
    assert v["250"] == 1 and v["15"] == 1 and v["1"] == 1 and v["12"] == 1


def test_alignment_finds_end_deletion_run():
    ref = "a b c d e f g h".split()
    hyp = "a b c d".split()
    r = runs(align(ref, hyp), "del")
    assert len(r) == 1 and r[0].ref == (4, 8) and r[0].length == 4


def test_alignment_wildcard_absorbs_number_words():
    ref = ["पानी", "100", "डिग्री"]
    hyp = ["पानी", "वन", "हंड्रेड", "डिग्री"]
    ops = align(ref, hyp, [False, True, False])
    assert error_rate(ops, len(ref)) == 0.0


def test_alignment_fuzzy_substitution_is_cheap_for_near_misses():
    ops = align(["वेप्पनिलैयिल"], ["वेप्पनिलयिल"])
    assert ops[0].kind == "sub" and ops[0].cost < 0.2


def test_markdown_structure_and_speakable():
    md = "## Title\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nArea $A = \\pi r^2$ and **bold**."
    st = structure(md)
    assert st.table_rows == [2, 2] and st.headings[2] == 1 and len(st.math) == 1 and st.bold_markers == 2
    plain, idx = speakable(md)
    assert "\\pi" not in plain and "|" not in plain and "Title" in plain
    assert all(md[j] == plain[i] for i, j in enumerate(idx) if plain[i] not in " ")
