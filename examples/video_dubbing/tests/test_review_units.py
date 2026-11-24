from bodhan_dub.review import parse_merges, review_boundaries, validate_merge
from bodhan_dub.sentences import build_sentences
from bodhan_dub.units import split_sentences


def _sents():
    chunks = [
        {"id": 1, "start": 0.0, "end": 2.0, "text": "But in this case."},
        {"id": 2, "start": 2.8, "end": 8.0, "text": "The bacterium could do something."},
        {"id": 3, "start": 9.0, "end": 11.0, "text": "Next point."},
    ]
    return build_sentences(chunks)


def test_parse_merges():
    r = parse_merges("MERGE 1,2|But in this case, the bacterium.\nnoise\nMERGE 5, 6 |x y")
    assert r == [([1, 2], "But in this case, the bacterium."), ([5, 6], "x y")]
    assert parse_merges("NONE") == []


def test_validate_rejects_changed_words_and_bad_ids():
    s = _sents()
    by = {x["id"]: x for x in s}
    assert validate_merge([1, 2], "But in this case, the bacterium could do something.", by) is None
    assert validate_merge([1, 2], "But in this case, the bacteria could do something.", by) == "words changed"
    assert validate_merge([1, 3], "x", by) == "not consecutive"
    assert validate_merge([1], "x", by) == "group size"
    assert validate_merge([1, 9], "x", by) == "unknown id"


def test_review_applies_valid_merge_and_keeps_times(tmp_path):
    llm = lambda m, **k: "MERGE 1,2|But in this case, the bacterium could do something."
    new, audit = review_boundaries(_sents(), tmp_path, llm=llm)
    assert len(new) == 2 and audit[0]["applied"]
    assert new[0]["start"] == 0.0 and new[0]["end"] == 8.0 and new[0]["timing"] == "exact"
    assert new[0]["chunk_ids"] == [1, 2] and [s["id"] for s in new] == [1, 2]


def test_review_rejects_bad_merge_and_keeps_original(tmp_path):
    llm = lambda m, **k: "MERGE 1,2|But in this case, the bacteria could do it."
    new, audit = review_boundaries(_sents(), tmp_path, llm=llm)
    assert len(new) == 3 and not audit[0]["applied"]


def test_review_survives_llm_failure(tmp_path):
    def boom(m, **k):
        raise RuntimeError("down")

    new, audit = review_boundaries(_sents(), tmp_path, llm=boom)
    assert len(new) == 3 and audit == []


def _long_sentence():
    text = "First part of it, second part of it is here, and then the third part follows, ending now."
    return build_sentences([{"id": 1, "start": 0.0, "end": 40.0, "text": text}])


def test_short_sentence_not_split():
    s = build_sentences([{"id": 1, "start": 0.0, "end": 5.0, "text": "Short one, really."}])
    u = split_sentences(s)
    assert len(u) == 1 and u[0]["split"] == "none"


def test_long_sentence_split_at_commas_within_limit():
    u = split_sentences(_long_sentence(), max_sec=20.0)
    assert len(u) >= 2
    assert all(x["end"] - x["start"] <= 20.0 + 1e-6 for x in u)
    assert u[0]["start"] == 0.0 and u[-1]["end"] == 40.0
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(u, u[1:]))
    assert " ".join(x["text"] for x in u) == _long_sentence()[0]["text"]


def test_unsplittable_long_sentence_is_flagged():
    s = build_sentences([{"id": 1, "start": 0.0, "end": 40.0, "text": "one two three four five six seven."}])
    u = split_sentences(s, max_sec=20.0)
    assert len(u) == 1 and "too_long_no_split" in u[0]["split_reasons"]
