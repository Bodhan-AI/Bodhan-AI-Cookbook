"""Combining candidate flags into final flags."""

from bodhan_eval.checks.base import Flag
from bodhan_eval.combine import apply_thresholds, combine, item_verdict, threshold_for
from bodhan_eval.params import Params

P = Params.defaults()


def F(cat, check, conf, span, sev="major"):
    return Flag(cat, sev, check, conf, span, f"{check} says {cat}")


def test_independent_checks_raise_confidence_noisy_or():
    out = combine([F("number", "numbers", 0.6, (10, 14)), F("number", "judge", 0.5, (10, 14))], P)
    assert len(out) == 1
    assert out[0].confidence == 0.8  # 1 - 0.4 * 0.5
    assert out[0].details["checks"] == ["judge", "numbers"]


def test_same_check_counted_once():
    out = combine(
        [
            F("omission", "backtranslation", 0.4, (0, 20)),
            F("omission", "backtranslation", 0.3, (5, 25)),
        ],
        P,
    )
    assert len(out) == 1 and out[0].confidence == 0.4


def test_category_vote_uses_reliability_and_keeps_disagreement():
    out = combine(
        [
            F("number", "numbers", 0.9, (10, 14)),
            F("mistranslation", "backtranslation", 0.5, (10, 15)),
        ],
        P,
    )
    assert len(out) == 1 and out[0].category == "number"
    assert out[0].confidence == 0.9  # the disagreeing check does not add confidence
    assert out[0].details["disagreeing"][0]["category"] == "mistranslation"


def test_separate_problems_stay_separate():
    flags = [
        F("number", "numbers", 0.9, (10, 14)),
        F("term", "terms", 0.6, (40, 50)),
        F("speech_omission", "speech", 0.7, (10, 14)),  # same span, other area
        F("speech_repetition", "audio", 0.35, None),
        F("speech_omission", "audio", 0.5, None),  # item-level, other category
    ]
    out = combine(flags, P)
    assert sorted(f.category for f in out) == [
        "number",
        "speech_omission",
        "speech_omission",
        "speech_repetition",
        "term",
    ]


def test_span_from_strongest_and_most_specific_flag():
    out = combine([F("number", "judge", 0.6, (8, 20)), F("number", "numbers", 0.9, (10, 14))], P)
    assert out[0].span == (10, 14)


def test_thresholds_and_verdict():
    p = P.with_overrides({"combine": {"thresholds": {"term": 0.7}}})
    merged = combine(
        [F("term", "terms", 0.6, (0, 5)), F("number", "numbers", 0.6, (9, 12), "minor")], p
    )
    assert threshold_for("term", p) == 0.7 and threshold_for("number", p) == 0.5
    kept = apply_thresholds(merged, p)
    assert [f.category for f in kept] == ["number"]
    assert item_verdict(kept) == {"verdict": "minor", "penalty": 1.0}
    assert item_verdict([]) == {"verdict": "clean", "penalty": 0.0}


def test_numbers_inside_an_added_sentence_are_absorbed():
    flags = [
        F("addition", "backtranslation", 0.6, (40, 80)),
        F("addition", "judge", 0.6, (40, 80)),
        F("number", "numbers", 0.6, (55, 57)),
        F("number", "numbers", 0.6, (60, 62)),
        F("number", "numbers", 0.9, (10, 14)),  # outside: a separate error
    ]
    out = combine(flags, P)
    assert sorted(f.category for f in out) == ["addition", "number"]
    add = next(f for f in out if f.category == "addition")
    assert len(add.details["absorbed"]) == 2
