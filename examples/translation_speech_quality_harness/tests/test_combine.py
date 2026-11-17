"""Combiner: one test row per line of the decision table in plan 07."""

import pytest

from bodhan_eval.checks_text import cand
from bodhan_eval.combine import combine, group, merge

S = (10, 20)  # a shared source position


def exact(check="numbers", cat="number", span=S, **kw):
    return cand(check, cat, "exact", 0.95, span, (span[0] // 2, span[1] // 2), **kw)


def noisy(check="back_translation", cat="omission", span=S, strength=0.6):
    return cand(check, cat, "noisy", strength, span, None)


def weak(check="length", cat="omission"):
    return cand(check, cat, "weak", 0.4)


def judge(cat="omission", sev="major", span=S):
    return cand("judge", cat, "judge", 0.85, span, None, sev)


@pytest.mark.parametrize("cands,verdict,status", [
    ([exact()], None, "flagged"),                                   # any exact candidate
    ([exact()], "clean", "flagged"),                                # exact beats a clean judge
    ([noisy(), noisy("regen_diff")], None, "flagged"),              # two different noisy checks agree
    ([noisy(), noisy("regen_diff")], "clean", "review"),            # ... but the judge disagrees: a human decides
    ([noisy(), judge()], None, "flagged"),                          # one noisy check plus the judge
    ([noisy(), judge(sev="minor")], None, "review"),                # judge says minor
    ([noisy(), weak()], None, "flagged"),                           # noisy plus a weak signal
    ([noisy()], None, "review"),                                    # one noisy check, nothing else
    ([noisy()], "clean", "clean"),                                  # one noisy check rejected by the judge: noise
    ([weak()], None, "clean"),                                      # weak signals only
    ([], None, "clean"),
])
def test_decision_table(cands, verdict, status):
    assert combine(cands, verdict)[0] == status


def test_noise_is_counted():
    assert combine([noisy()], "clean")[3] == 1


def test_one_error_seen_by_three_checks_is_one_flag():
    status, flags, review, _ = combine([exact(), noisy("regen_diff", "mistranslation"), judge("number", "critical")])
    assert status == "flagged" and len(flags) == 1 and not review
    f = flags[0]
    assert f["category"] == "number" and f["severity"] == "critical"
    assert f["checks"] == ["judge", "numbers", "regen_diff"] and f["span"] == list(S) and f["span_in"] == "source"


def test_separate_positions_stay_separate():
    _, flags, _, _ = combine([exact(span=(0, 5)), exact("format", "format", span=(30, 40))])
    assert [f["category"] for f in flags] == ["number", "format"]


def test_text_and_speech_never_merge():
    speech = cand("transcript", "speech_omission", "noisy", 0.7, S, None)
    assert len(group([noisy(), speech])) == 2


def test_unpositioned_weak_joins_same_category():
    assert len(group([noisy(), weak()])) == 1


def test_judge_category_wins_over_diff_guess():
    f = merge([noisy("regen_diff", "mistranslation"), judge("term")])
    assert f["category"] == "term"


def test_narrowest_span_from_strongest_class():
    f = merge([cand("numbers", "number", "exact", 0.9, (12, 14), None), noisy(cat="number", span=(0, 40))])
    assert f["source_span"] == [12, 14]


def test_confidence_combines_independent_checks():
    one = merge([noisy(strength=0.6)])["confidence"]
    two = merge([noisy(strength=0.6), noisy("regen_diff", strength=0.6)])["confidence"]
    assert one == 0.6 and two == 0.84


def test_output_order_is_deterministic():
    a = [exact("format", "format", span=(30, 40)), exact(span=(0, 5))]
    assert combine(a) == combine(list(reversed(a)))
