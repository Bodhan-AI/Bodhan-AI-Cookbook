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
    ([judge(sev="critical")], None, "review"),                      # the judge alone never flags
    ([judge(), weak()], None, "flagged"),                           # the judge plus a weak signal
    ([noisy(), weak()], None, "flagged"),                           # noisy plus a weak signal
    ([noisy()], None, "review"),                                    # one noisy check, nothing else
    ([noisy()], "clean", "clean"),                                  # one noisy check rejected by the judge: noise
    ([weak()], None, "clean"),                                      # weak signals only
    ([], None, "clean"),
])
def test_decision_table(cands, verdict, status):
    assert combine(cands, verdict)[0] == status


def test_clean_text_verdict_does_not_veto_speech():
    rep = cand("transcript", "speech_repetition", "noisy", 0.6, None, S)
    again = cand("transcript_recheck", "speech_repetition", "noisy", 0.6, None, S)
    assert combine([rep], "clean")[0] == "review"
    assert combine([rep, again], "clean")[0] == "flagged"


def test_judge_category_backed_by_other_checks_wins():
    g = [judge("omission"), judge("addition"), noisy("back_translation", "addition"), noisy("regen_diff", "addition")]
    assert merge(g)["category"] == "addition"


def test_extra_formula_inside_an_added_sentence_is_an_addition():
    fmt = cand("format", "format", "exact", 0.95, S, S, extra=["$r$"], missing=[])
    assert merge([fmt, noisy("back_translation", "addition")])["category"] == "addition"
    assert merge([fmt])["category"] == "format"  # nothing else says addition
    lost = cand("format", "format", "exact", 0.95, S, S, extra=[], missing=["$r$"])
    assert merge([lost, noisy("back_translation", "addition")])["category"] == "format"  # something was lost: format


def test_name_on_the_source_side_makes_a_term():
    from bodhan_eval.combine import is_name
    src = "Regulators said SEC Chair Gary Gensler met Kiev officials."
    span = lambda w: (src.index(w), src.index(w) + len(w))  # noqa: E731
    assert is_name(src, span("Gary Gensler")) and is_name(src, span("SEC")) and is_name(src, span("Kiev"))
    assert not is_name(src, span("Regulators")) and not is_name(src, span("officials")) and not is_name(src, None)


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


def test_noisy_group_takes_category_most_checks_agree_on():
    from bodhan_eval.checks_text import cand
    from bodhan_eval.combine import merge

    g = [cand("back_translation", "omission", "noisy", 0.6, (0, 40), None), cand("regen_diff", "omission", "noisy", 0.7, (0, 40), (0, 0)),
         cand("numbers", "number", "noisy", 0.4, (10, 12), None), cand("numbers", "number", "noisy", 0.4, (20, 22), None)]
    assert merge(g)["category"] == "omission"
    assert merge(g[2:] + [cand("length", "omission", "noisy", 0.5, None, None)])["category"] == "number"  # 1 vs 1: precedence


def test_span_comes_from_the_flags_own_category():
    from bodhan_eval.checks_text import cand
    from bodhan_eval.combine import merge

    g = [cand("back_translation", "addition", "noisy", 0.6, None, (10, 60)), cand("regen_diff", "addition", "noisy", 0.7, None, (10, 60)),
         cand("numbers", "number", "noisy", 0.5, None, (30, 32))]
    f = merge(g)
    assert f["category"] == "addition" and tuple(f["translation_span"]) == (10, 60)


def test_severity_rules_negation_and_single_word():
    from bodhan_eval.combine import adjust_severity

    src = "Children must not take this dose."
    n = src.index("not")
    f = {"category": "omission", "severity": "major", "source_span": [n, n + 3], "translation_span": None, "evidence": [{"class": "noisy"}]}
    assert adjust_severity(dict(f), src, "x")["severity"] == "critical"
    word = {**f, "source_span": [0, 8]}
    assert adjust_severity(dict(word), src, "x")["severity"] == "minor"
    judged = {**word, "evidence": [{"class": "judge"}]}
    assert adjust_severity(dict(judged), src, "x")["severity"] == "major"
