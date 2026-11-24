import copy
import json

import pytest

from bodhan_eval.align import GAP, align, align_item, align_item_by_segment, project_span, similarity, word_diff
from bodhan_eval.segment import segments
from test_clients import TRANSLATE_OK, make

SRC = ["The museum opens at 10 am.", "Entry costs 50 rupees for adults.", "Children enter free.", "Dr.", "Meera Rao said this."]
GOOD = ["The museum opens at 10 am.", "The entry fee for adults is Rs 50.", "Children are admitted free of charge.", "Dr Meera Rao said this."]


def shape(pairs):
    return [(p.src, p.back) for p in pairs]


def test_faithful_back_translation_is_all_matches():
    pairs = align(SRC, GOOD)
    assert shape(pairs) == [((0,), (0,)), ((1,), (1,)), ((2,), (2,)), ((3, 4), (3,))]  # "Dr." + "Meera..." joined
    assert all(p.score > GAP for p in pairs)


@pytest.mark.parametrize("drop", [0, 1, 4])
def test_dropped_sentence_is_an_unmatched_source_sentence(drop):
    back = [s for k, s in enumerate(["The museum opens at 10 am.", "The entry fee for adults is Rs 50.",
                                     "Children are admitted free of charge.", "Dr.", "Meera Rao said this."]) if k != drop]
    omitted = [p.src for p in align(SRC, back) if not p.back]
    assert omitted == [(drop,)]


def test_added_sentence_is_an_unmatched_back_sentence():
    back = GOOD[:2] + ["The building was painted blue last year."] + GOOD[2:]
    assert [p.back for p in align(SRC, back) if not p.src] == [(2,)]


def test_merged_sentences_are_not_errors():
    back = ["The museum opens at 10 am and entry for adults costs Rs 50.", "Children are admitted free of charge, said Dr Meera Rao."]
    pairs = align(SRC, back)
    assert shape(pairs) == [((0, 1), (0,)), ((2, 3, 4), (1,))]
    assert all(p.src and p.back for p in pairs)


def test_reworded_or_wrong_text_is_one_weak_match_not_omission_plus_addition():
    reword = ["At 10 in the morning the museum is opened.", "Grown-ups pay Rs 50 to get in.", "Kids get in without paying.",
              "This was told by Dr Meera Rao."]
    for back in (reword, GOOD[:1] + ["The river flooded the old bridge."] + GOOD[2:]):
        pairs = align(SRC, back)
        assert all(p.src and p.back for p in pairs)  # no one-sided gaps
        assert any(p.score < GAP for p in pairs)  # the weak match is visible for the judge


def test_empty_sides():
    assert shape(align(SRC, [])) == [((0, 1, 2, 3, 4), ())]
    assert shape(align([], ["x."])) == [((), (0,))]
    assert align([], []) == []


def test_similarity_ignores_punctuation_case_and_digit_script():
    assert similarity("Water boils at 100 °C.", "water boils at १०० °C") == 1.0
    assert similarity("Water boils.", "") == 0.0
    assert similarity("Entry costs 50 rupees.", "The river flooded.") < GAP < similarity("Entry costs 50 rupees.", "Entry is 50 rupees.")


def test_word_diff_points_at_source_words():
    src = "Entry costs ५० rupees for adults."
    d = word_diff(src, "Entry costs 50 rupees for children.")
    assert [src[s:e] for s, e in d.missing] == ["adults"] and d.extra == ["children"]
    d = word_diff(src, "Entry costs 50 rupees.")
    assert [src[s:e] for s, e in d.missing] == ["for adults"] and d.extra == []


def test_segments_skip_markup_code_math_and_keep_offsets():
    text = ("## Results\n\n| Metric | Value |\n|---|---|\n| Accuracy | 92% |\n\n$$E = mc^2$$\n\n"
            "```\nprint('Hello.')\n```\n- Dr. Rao said **fast**. Done!\n1. 42 %")
    segs = segments(text)
    assert [s.text for s in segs] == ["Results", "Metric | Value", "Accuracy | 92%", "Dr.", "Rao said **fast**.", "Done!"]
    assert all(text[s.start:s.end] == s.text for s in segs)


def bt_response(text):
    r = copy.deepcopy(TRANSLATE_OK)
    r[1]["choices"][0]["message"]["content"] = text
    return r


def test_align_item_uses_one_call_and_splits_the_back_translation(tmp_path):
    merged = "The museum opens at 10 am. Children are admitted free of charge, said Dr Meera Rao."  # Hindi-style merge
    clients, server = make(tmp_path, {"/v1/chat/completions": [bt_response(merged)]})
    source = " ".join(SRC)
    al = align_item(source, "संग्रहालय ... (Hindi)", clients)
    assert len(server.requests) == 1 and json.loads(server.requests[0].content)["target_language_code"] == "en"
    omitted = [p for p in al.pairs if not p.back]
    assert [source[al.source[i].start:al.source[i].end] for p in omitted for i in p.src] == ["Entry costs 50 rupees for adults."]


def test_by_segment_mode_projects_source_spans_onto_the_translation(tmp_path):
    translation = "संग्रहालय सुबह 10 बजे खुलता है। बच्चों का प्रवेश निःशुल्क है।"  # the entry-fee sentence is missing
    clients, server = make(tmp_path, {"/v1/chat/completions": [
        bt_response("The museum opens at 10 am."), bt_response("Children are admitted free of charge.")]})
    source = "The museum opens at 10 am. Entry costs 50 rupees for adults. Children enter free."
    al = align_item_by_segment(source, translation, clients)
    assert len(server.requests) == 2

    s, e = project_span(al, 0, 26)  # "The museum opens at 10 am."
    assert translation[s:e] == "संग्रहालय सुबह 10 बजे खुलता है।"
    s, e = project_span(al, 27, 60)  # the omitted sentence -> insertion point after the first sentence
    assert s == e == translation.index("।") + 1
    assert project_span(align_item(source, translation, make(tmp_path / "2", {"/v1/chat/completions": [
        bt_response("The museum opens at 10 am. Children are admitted free of charge.")]})[0]), 0, 26) is None
