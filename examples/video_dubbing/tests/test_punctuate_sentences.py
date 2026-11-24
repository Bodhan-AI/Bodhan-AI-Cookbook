from bodhan_dub.punctuate import add_pauses, parse_reply, punctuate_chunks, rule_punctuate, words_unchanged
from bodhan_dub.sentences import build_sentences, is_sentence_end


def test_words_unchanged_ignores_case_and_punct():
    assert words_unchanged("SO RIGHT NOW IT'S ACTUALLY", "So right now, it's actually,")


def test_words_unchanged_rejects_changed_word():
    assert not words_unchanged("A INNER MEMBRANE", "An inner membrane")
    assert not words_unchanged("ONE TWO THREE", "One two.")


def test_words_unchanged_allows_space_merge():
    assert words_unchanged("THE E R", "The ER.")


def test_parse_reply_ignores_noise():
    assert parse_reply("Here you go:\n3|Hello there.\n 4 | pause=0.5 | Bye.") == {3: "Hello there.", 4: "Bye."}


def test_rule_punctuate():
    assert rule_punctuate("HELLO THERE", 1.0) == "Hello there."
    assert rule_punctuate("hello i think", 0.0) == "Hello I think"


def test_add_pauses_recovers_real_gap():
    raw = [{"id": 1, "start": 0, "end": 2, "text": "a"}, {"id": 2, "start": 2.3, "end": 4, "text": "b"}]
    p = add_pauses(raw)
    assert p[0]["pause_after"] == 0.6 and p[1]["pause_after"] == 9.9


def test_punctuate_falls_back_when_llm_changes_words(tmp_path):
    raw = [{"id": 1, "start": 0, "end": 2, "text": "A INNER MEMBRANE", "ok": True}]
    out = punctuate_chunks(raw, tmp_path, llm=lambda m: "1|An inner membrane.", max_attempts=2)
    assert out[0]["punct_method"] == "rule" and out[0]["text"] == "A inner membrane."


def test_punctuate_accepts_valid_llm(tmp_path):
    raw = [{"id": 1, "start": 0, "end": 2, "text": "HELLO WORLD", "ok": True}]
    out = punctuate_chunks(raw, tmp_path, llm=lambda m: "1|Hello, world.")
    assert out[0]["punct_method"] == "llm" and out[0]["text"] == "Hello, world."


def test_sentence_end_detection():
    assert is_sentence_end("year.") and is_sentence_end("what?") and is_sentence_end('end."')
    assert not is_sentence_end("Dr.") and not is_sentence_end("however,")


def test_sentence_spanning_chunks_is_merged():
    chunks = [
        {"id": 1, "start": 0.0, "end": 2.0, "text": "It's actually,"},
        {"id": 2, "start": 2.5, "end": 6.5, "text": "the situation a cell faced."},
    ]
    s = build_sentences(chunks)
    assert len(s) == 1
    assert s[0]["start"] == 0.0 and s[0]["end"] == 6.5 and s[0]["chunk_ids"] == [1, 2]
    assert s[0]["timing"] == "exact"


def test_two_sentences_in_one_chunk_split_time_by_chars():
    chunks = [{"id": 1, "start": 0.0, "end": 10.0, "text": "Aaaa aaaa. Bbbb bbbb."}]
    s = build_sentences(chunks)
    assert len(s) == 2
    assert s[0]["start"] == 0.0 and s[1]["end"] == 10.0
    assert 4.0 < s[0]["end"] < 6.0 and s[0]["timing"] == "estimated"


def test_trailing_text_without_period_is_kept():
    s = build_sentences([{"id": 1, "start": 0, "end": 1, "text": "and then"}])
    assert len(s) == 1 and s[0]["text"] == "and then"


def test_failed_chunk_skipped():
    s = build_sentences([{"id": 1, "start": 0, "end": 1, "text": ""}, {"id": 2, "start": 2, "end": 3, "text": "Hi."}])
    assert len(s) == 1 and s[0]["chunk_ids"] == [2]
