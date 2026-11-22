"""Transcript repair: batching, the validator's guardrails, and retry-then-fallback."""

import pytest

from bodhan_dub.llm import LLMError
from bodhan_dub.repair import (
    RepairParams,
    basic_cleanup,
    fidelity,
    make_batches,
    repair_transcript,
    validate_units,
)
from bodhan_dub.transcribe import Segment

P = RepairParams()


def seg(i, start, end, text, **kw):
    return Segment(id=f"s{i:04d}", start=start, end=end, text=text, **kw)


BATCH = [
    seg(0, 0.0, 5.0, "today we learn about angles an angle is formed"),
    seg(1, 5.3, 9.0, "by two rays with a common end point"),
    seg(2, 12.0, 16.0, "right it in your notebook"),
]


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def complete_json(self, system, user):
        self.prompts.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


GOOD = {"units": [
    {"ids": ["s0000", "s0001"],
     "text": "Today we learn about angles. An angle is formed by two rays with a common end point."},
    {"ids": ["s0002"], "text": "Write it in your notebook."},
]}


def test_good_reply_becomes_timed_units():
    units, errors = validate_units(GOOD, BATCH, P)
    assert errors == []
    assert [(u.start, u.end, u.source_ids) for u in units] == [(0.0, 9.0, ["s0000", "s0001"]), (12.0, 16.0, ["s0002"])]
    assert units[1].raw_text == "right it in your notebook"


@pytest.mark.parametrize("reply, expected", [
    ({"units": [{"ids": ["s0000", "s0001"], "text": "x y"}]}, "missing"),
    ({"units": [{"ids": ["s0000"], "text": "a"}, {"ids": ["s0000", "s0001", "s0002"], "text": "b"}]}, "more than once"),
    ({"units": [{"ids": ["s0000", "s0002"], "text": "a"}, {"ids": ["s0001"], "text": "b"}]}, "not consecutive"),
    ({"units": [{"ids": ["s0009"], "text": "a"}]}, "unknown ids"),
    ({"units": [{"ids": ["s0000", "s0001"], "text": GOOD["units"][0]["text"]},
                {"ids": ["s0002"], "text": ""}]}, "empty text"),
    ("not a dict", "reply must be"),
])
def test_validator_rejects_malformed_replies(reply, expected):
    _, errors = validate_units(reply, BATCH, P)
    assert any(expected in e for e in errors), errors


def test_validator_rejects_merge_across_a_long_pause():
    reply = {"units": [{"ids": ["s0000", "s0001", "s0002"],
                        "text": GOOD["units"][0]["text"] + " " + GOOD["units"][1]["text"]}]}
    _, errors = validate_units(reply, BATCH, P)
    assert any("3.0s gap" in e for e in errors)


def test_validator_rejects_overlong_units():
    long_batch = [seg(0, 0.0, 14.0, "a b c d e f"), seg(1, 14.5, 28.0, "g h i j k l")]
    reply = {"units": [{"ids": ["s0000", "s0001"], "text": "A b c d e f g h i j k l."}]}
    _, errors = validate_units(reply, long_batch, P)
    assert any("spans 28.0s" in e for e in errors)


def test_validator_rejects_paraphrase_but_accepts_light_fixes():
    paraphrase = {"units": [
        {"ids": ["s0000", "s0001"], "text": "In this lesson we study how two rays meeting at a point create an angle."},
        {"ids": ["s0002"], "text": "Write it in your notebook."}]}
    _, errors = validate_units(paraphrase, BATCH, P)
    assert any("drifts from what was said" in e for e in errors)
    assert fidelity("right it in your notebook", "Write it in your notebook.") >= P.min_fidelity_short


def test_fidelity_ignores_how_numbers_are_written():
    assert fidelity("it is one hundred and eighty degrees", "It is 180 degrees.") == 1.0
    assert fidelity("three sixty minus one twenty", "360 minus 120") == 1.0


def test_dropped_units_need_no_text_and_are_kept_with_reason():
    reply = {"units": [{"ids": ["s0000", "s0001"], "text": GOOD["units"][0]["text"]},
                       {"ids": ["s0002"], "text": "", "drop": True, "reason": "music lyrics"}]}
    units, errors = validate_units(reply, BATCH, P)
    assert errors == [] and units[1].dropped == "music lyrics" and units[1].text == ""


def test_invalid_reply_is_retried_with_the_errors_then_accepted():
    llm = ScriptedLLM({"units": [{"ids": ["s0000"], "text": "x"}]}, GOOD)
    units, report = repair_transcript(llm, BATCH, P)
    assert report.llm_ok == 1 and report.retried == 1 and report.fell_back == 0
    assert "Your previous reply was rejected" in llm.prompts[1] and "missing" in llm.prompts[1]
    assert [u.id for u in units] == ["u0000", "u0001"]


def test_two_bad_replies_fall_back_to_raw_segmentation():
    llm = ScriptedLLM({"units": []}, {"units": []})
    units, report = repair_transcript(llm, BATCH, P)
    assert report.fell_back == 1
    assert [u.text for u in units] == ["Today we learn about angles an angle is formed.",
                                       "By two rays with a common end point.", "Right it in your notebook."]
    assert all(u.repaired_by == "fallback" for u in units)


def test_llm_outage_falls_back_without_retrying():
    llm = ScriptedLLM(LLMError("provider down"))
    units, report = repair_transcript(llm, BATCH, P)
    assert report.fell_back == 1 and len(llm.prompts) == 1 and len(units) == 3


def test_skipped_segments_never_reach_the_llm():
    segments = [*BATCH, seg(3, 20.0, 22.0, "", skipped="no speech recognised")]
    assert [s.id for b in make_batches(segments, P) for s in b] == ["s0000", "s0001", "s0002"]


def test_batches_cut_only_where_merging_is_not_allowed():
    # 70 segments; a long pause after every 7th one. Batches must end right before a long pause.
    segments, t = [], 0.0
    for i in range(70):
        segments.append(seg(i, t, t + 2.0, f"word{i}"))
        t += 2.0 + (3.0 if i % 7 == 6 else 0.4)
    batches = make_batches(segments, RepairParams(batch_size=30))
    assert sum(len(b) for b in batches) == 70
    assert all(len(b) <= 30 for b in batches)
    for b in batches[:-1]:
        assert int(b[-1].id[1:]) % 7 == 6


def test_basic_cleanup():
    assert basic_cleanup("LEANIER PAIR NO") == "Leanier pair no."
    assert basic_cleanup("  is it  ") == "Is it."
    assert basic_cleanup("Done?") == "Done?"


def test_failed_batch_is_split_at_its_widest_pause_and_each_half_repaired():
    segments, t = [], 0.0
    for i in range(12):
        segments.append(seg(i, t, t + 2.0, f"word number {i}"))
        t += 2.0 + (2.5 if i == 5 else 0.4)  # the widest pause is after s0005

    class CutOffForBigBatches:
        def __init__(self):
            self.sizes = []

        def complete_json(self, system, user):
            import json
            rows = json.loads(user.split("\n\n")[0])["segments"]
            self.sizes.append(len(rows))
            if len(rows) > 6:
                raise LLMError("LLM reply cut off at the token limit")
            return {"units": [{"ids": [r["id"]], "text": r["text"].capitalize() + "."} for r in rows]}

    llm = CutOffForBigBatches()
    units, report = repair_transcript(llm, segments, RepairParams())
    assert llm.sizes == [12, 6, 6] and report.splits == 1 and report.fell_back == 0
    assert [u.source_ids[0] for u in units][:7] == [f"s{i:04d}" for i in range(7)]
    assert all(u.repaired_by == "llm" for u in units)
