"""The plan and shortening calls: few LLM calls, each answer checked on its own, failures stay local."""

import json

from bodhan_dub.llm import LLMError, LLMTruncated
from bodhan_dub.speech import SHORTEN_BATCH, shorten_all
from bodhan_dub.transcript import MAX_GAP, plan, plan_windows


class ScriptedLLM:
    """Answers each prompt with answer(prompt); records the prompts it was sent."""

    def __init__(self, answer):
        self.answer, self.prompts, self.down = answer, [], ""

    def ask_json(self, prompt):
        self.prompts.append(prompt)
        reply = self.answer(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply


def sent(prompt):
    """The JSON sentence lines a shortening prompt carries."""
    return [json.loads(line) for line in prompt.splitlines() if line.startswith('{"id"')]


def test_all_sentences_are_shortened_in_one_call():
    llm = ScriptedLLM(lambda p: {"sentences": [{"id": s["id"], "text": f"short {s['id']}"} for s in sent(p)]})
    items = [(f"a long sentence number {i}", 3, 1) for i in range(5)]
    assert shorten_all(items, llm) == [f"short {i}" for i in range(5)]
    assert len(llm.prompts) == 1


def test_large_batches_are_split():
    llm = ScriptedLLM(lambda p: {"sentences": [{"id": s["id"], "text": "ok"} for s in sent(p)]})
    shorten_all([("some text", 3, 1)] * (SHORTEN_BATCH + 1), llm)
    assert len(llm.prompts) == 2


def test_only_answers_that_changed_numbers_are_retried():
    def answer(prompt):
        if "changed a number" in prompt:
            return {"sentences": [{"id": s["id"], "text": "Angles of 90 and 45."} for s in sent(prompt)]}
        return {"sentences": [{"id": 0, "text": "Fine."}, {"id": 1, "text": "Angles of 90 and 40."}]}

    llm = ScriptedLLM(answer)
    assert shorten_all([("This is fine.", 2, 1), ("The angles are 90 and 45 degrees.", 4, 1)], llm) == \
        ["Fine.", "Angles of 90 and 45."]
    assert [s["id"] for s in sent(llm.prompts[1])] == [1]
    assert sorted(sent(llm.prompts[1])[0]["numbers"]) == [45, 90]


def test_malformed_and_missing_answers_become_none():
    llm = ScriptedLLM(lambda p: {"sentences": [{"id": "0", "text": "ok"}, {"text": "no id"}, "junk"]})
    assert shorten_all([("alpha", 3, 1), ("beta", 3, 1)], llm) == ["ok", None]


def test_a_failed_call_means_no_shortening():
    llm = ScriptedLLM(lambda p: LLMError("down"))
    assert shorten_all([("one", 3, 1), ("two", 3, 1)], llm) == [None, None]


def segments(texts_and_gaps):
    """Segments of 1 s each, separated by the given pauses."""
    out, t = [], 0.0
    for i, (text, gap) in enumerate(texts_and_gaps):
        out.append({"id": i, "start": t, "end": t + 1.0, "text": text})
        t += 1.0 + gap
    return out


def test_windows_cover_every_segment_in_order_and_respect_the_size():
    segs = segments([("x" * 40, 0.3)] * 50)
    windows = plan_windows(segs, max_chars=400)
    assert [s["id"] for w in windows for s in w] == list(range(50))
    assert all(sum(len(s["text"]) + 1 for s in w) <= 400 + 1 for w in windows)


def test_windows_are_cut_at_the_longest_pause():
    gaps = [0.3] * 12
    gaps[7] = 2.5  # the pause after segment 7
    windows = plan_windows(segments([("y" * 40, g) for g in gaps]), max_chars=400)
    assert windows[0][-1]["id"] == 7


def test_a_short_transcript_is_one_window():
    assert len(plan_windows(segments([("hello there", 0.3)] * 5))) == 1


def test_a_failed_window_falls_back_alone_and_terms_carry_forward(tmp_path, monkeypatch):
    monkeypatch.setattr("bodhan_dub.transcript.PLAN_CHARS", 100)
    segs = segments([("the vertex o", 0.3), ("is shared", MAX_GAP + 1), ("second part here", 0.3), ("and ends", 0.3)])

    def answer(prompt):
        if "\n0 [" in prompt:
            return {"segments": [{"id": 0, "text": "The vertex O", "ends": False},
                                 {"id": 1, "text": "is shared.", "ends": True}],
                    "glossary": [{"term": "vertex", "keep": False}]}
        return LLMError("down")

    llm = ScriptedLLM(answer)
    monkeypatch.setattr("bodhan_dub.transcript.plan_windows", lambda s: plan_windows(s, max_chars=30))
    units, glossary = plan(segs, tmp_path, llm)
    assert len(llm.prompts) == 4  # window 2-3 failed, then 2 and 3 alone
    assert all("vertex" in prompt for prompt in llm.prompts[1:])
    assert units[0]["text"] == "The vertex O is shared."
    assert [u["text"] for u in units[1:]] == ["second part here", "and ends"]
    assert [t["term"] for t in glossary] == ["vertex"]


def test_a_batch_cut_off_at_the_output_limit_is_split_until_it_fits():
    def answer(prompt):
        batch = sent(prompt)
        if len(batch) > 3:
            return LLMTruncated("cut off")
        return {"sentences": [{"id": b["id"], "text": "short " + b["text"][-1]} for b in batch]}

    llm = ScriptedLLM(answer)
    letters = [chr(97 + i) for i in range(12)]
    assert shorten_all([(f"sentence {c}", 3, 1) for c in letters], llm) == [f"short {c}" for c in letters]
    assert len(llm.prompts) == 1 + 2 + 4  # 12 -> 6 + 6 -> 3 + 3 + 3 + 3


def test_a_window_cut_off_at_the_output_limit_is_split_until_it_fits(tmp_path):
    segs = segments([(f"part {chr(97 + i)}", 0.3) for i in range(6)])

    def answer(prompt):
        ids = [int(line.split()[0]) for line in prompt.split("Segments:\n")[1].splitlines()]
        if len(ids) > 2:
            return LLMTruncated("cut off")
        return {"segments": [{"id": i, "text": f"Part {chr(97 + i)}.", "ends": True} for i in ids]}

    units, _ = plan(segs, tmp_path, ScriptedLLM(answer))
    assert [u["text"] for u in units] == [f"Part {chr(97 + i)}." for i in range(6)]


def test_shortening_may_drop_a_one_but_no_other_number():
    from bodhan_dub.speech import numbers_kept
    assert numbers_kept("Name a linear pair.", "Name one linear pair of the 2 lines.") is False
    assert numbers_kept("Name a pair of the 2 lines.", "Name one linear pair of the 2 lines.")
    assert not numbers_kept("Angles under 90.", "Angles under 90 and over 180.")


def test_an_answer_that_cuts_too_much_is_retried_then_dropped():
    def answer(prompt):
        return {"sentences": [{"id": s["id"], "text": "Too short."} for s in sent(prompt)]}

    llm = ScriptedLLM(answer)
    source = "Two adjacent angles whose arms form a line make a linear pair here"
    assert shorten_all([(source, 8, 9)], llm) == [None]
    assert len(llm.prompts) == 2 and sent(llm.prompts[0])[0]["min_words"] == 9


def test_the_floor_is_a_share_of_the_original_sentence():
    from bodhan_dub.speech import min_words
    assert min_words("one two three four five six seven eight nine ten") == 6
    assert min_words("tiny") == 3
