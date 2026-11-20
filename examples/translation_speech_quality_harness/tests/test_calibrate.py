"""Threshold calibration on synthetic labelled items with a known best answer."""

from bodhan_eval.calibrate import CalItem, LabelRec, calibrate, evaluate, match_item, span_match
from bodhan_eval.checks.base import Flag
from bodhan_eval.params import Params

P = Params.defaults()


def F(cat, conf, span=(10, 20)):
    return Flag(cat, "major", "combined", conf, span, "")


def injected(i, cat, conf, span=(10, 20)):
    return CalItem(f"inj{i}", "injected", [LabelRec(cat, span)], [F(cat, conf, span)])


def clean(i, flags=()):
    return CalItem(f"c{i}", "assumed_clean", [], list(flags))


def test_span_match_rules():
    assert span_match(LabelRec("number", (10, 14)), F("number", 0.9, (12, 13)), 3)
    assert not span_match(LabelRec("number", (10, 14)), F("term", 0.9, (10, 14)), 3)
    assert not span_match(LabelRec("number", (10, 14)), F("number", 0.9, (30, 40)), 3)
    # zero-width omission label: the flag must touch the deletion point
    assert span_match(LabelRec("omission", (25, 25)), F("omission", 0.6, (0, 24)), 3)
    assert not span_match(LabelRec("omission", (25, 25)), F("omission", 0.6, (40, 60)), 3)


def test_injected_items_do_not_count_unrelated_flags_as_fp():
    it = CalItem(
        "x",
        "injected",
        [LabelRec("number", (10, 14))],
        [F("number", 0.9, (10, 14)), F("term", 0.9, (50, 60))],
    )
    ev = evaluate([it], {"number": 0.5, "term": 0.5})
    assert ev["per_category"]["number"]["tp"] == 1 and ev["per_category"]["term"]["fp"] == 0
    complete = CalItem("y", "complete", it.labels, it.candidates)
    assert evaluate([complete], {"number": 0.5, "term": 0.5})["per_category"]["term"]["fp"] == 1


def test_one_flag_matches_one_label():
    it = CalItem(
        "x",
        "complete",
        [LabelRec("number", (10, 14)), LabelRec("number", (12, 16))],
        [F("number", 0.9, (10, 16))],
    )
    pairs, extra, missed = match_item(it, {"number": 0.5})
    assert len(pairs) == 1 and len(missed) == 1 and not extra


def test_calibrate_lowers_threshold_to_catch_true_errors():
    # term errors are detected at 0.45: below the 0.5 default, and clean items never get one
    items = [injected(i, "term", 0.45) for i in range(5)] + [clean(i) for i in range(10)]
    cal = calibrate(items, P)
    assert cal["thresholds"]["term"] <= 0.45
    assert cal["metrics"]["per_category"]["term"]["recall"] == 1.0
    assert cal["notes"]["term"] == "calibrated"


def test_calibrate_respects_clean_item_budget():
    # true errors at 0.6, but 3 of 10 clean items also carry a 0.55 flag of the same kind
    items = [injected(i, "omission", 0.6) for i in range(5)]
    items += [clean(i, [F("omission", 0.55)]) for i in range(3)] + [clean(i + 3) for i in range(7)]
    cal = calibrate(items, P)
    t = cal["thresholds"]["omission"]
    assert 0.55 < t <= 0.6
    assert cal["metrics"]["clean_flag_rate"] == 0.0


def test_sparse_categories_keep_default():
    cal = calibrate([injected(0, "format", 0.2), clean(0)], P)
    assert cal["thresholds"]["format"] == 0.5 and "kept default" in cal["notes"]["format"]


def test_ties_prefer_the_default_threshold():
    # every threshold in (0, 0.82] separates these perfectly: keep the 0.5 prior
    items = [injected(i, "addition", 0.82) for i in range(3)] + [clean(i) for i in range(5)]
    assert calibrate(items, P)["thresholds"]["addition"] == 0.5
