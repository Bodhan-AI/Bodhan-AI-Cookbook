"""Calibration on toy labelled sets: thresholds move only with enough labels, and metrics are counted right."""

import json

from bodhan_eval.calibrate import MIN_CLEAN, MIN_POSITIVE, apply, canon, collect, evaluate, fit_thresholds, wilson
from bodhan_eval.checks_text import cand


def res(i, cands=(), flags=()):
    return {"id": str(i), "lang": "hi", "_cands": list(cands), "flags": list(flags)}


def noisy_bt(strength, span=(0, 10)):
    return cand("back_translation", "omission", "noisy", strength, span, None)


def toy(n_err, n_clean, clean_strength=0.5, err_strength=0.8):
    """n_err items with a labelled omission the check sees strongly; n_clean clean items where it fires weakly."""
    results, labels = [], {}
    for i in range(n_err):
        results.append(res(i, [noisy_bt(err_strength)]))
        labels[(str(i), "hi")] = [{"type": "omission", "category": "omission", "span": [2, 6]}]
    for i in range(n_err, n_err + n_clean):
        results.append(res(i, [noisy_bt(clean_strength)]))
        labels[(str(i), "hi")] = []
    return results, labels


def test_threshold_moves_with_enough_labels():
    results, labels = toy(MIN_POSITIVE, MIN_CLEAN)
    t = fit_thresholds(results, labels)["back_translation:omission"]
    assert t["moved"] and 0.5 < t["calibrated"] <= 0.8
    assert "keeps 5 of 5" in t["reason"]


def test_threshold_kept_with_too_few_labels():
    results, labels = toy(MIN_POSITIVE - 1, MIN_CLEAN)
    assert not fit_thresholds(results, labels)["back_translation:omission"]["moved"]
    results, labels = toy(MIN_POSITIVE, MIN_CLEAN - 1)
    assert not fit_thresholds(results, labels)["back_translation:omission"]["moved"]


def test_threshold_stays_when_check_is_quiet_on_clean():
    results, labels = toy(MIN_POSITIVE, MIN_CLEAN)
    for r in results[MIN_POSITIVE:]:
        r["_cands"] = []
    t = fit_thresholds(results, labels)["back_translation:omission"]
    assert t["calibrated"] == 0.0 and not t["moved"]


def test_apply_drops_below_threshold_only():
    th = {"back_translation:omission": {"calibrated": 0.6}}
    kept = apply([noisy_bt(0.5), noisy_bt(0.7), cand("numbers", "number", "exact", 0.1)], th)
    assert [c["strength"] for c in kept] == [0.7, 0.1]


def test_evaluate_counts():
    flag = {"category": "omission", "span": [0, 10], "source_span": [0, 10], "translation_span": None}
    wrong_cat = {**flag, "category": "addition"}
    results = [res(0, flags=[flag]), res(1, flags=[wrong_cat]), res(2), res(3, flags=[flag]), res(4)]
    lab = [{"type": "omission", "category": "omission", "span": [2, 6]}]
    labels = {("0", "hi"): lab, ("1", "hi"): lab, ("2", "hi"): lab, ("3", "hi"): [], ("4", "hi"): []}
    o = evaluate(results, labels, {}, {}, "source")["overall"]
    assert o["recall"] == round(2 / 3, 3) and o["false_alarm_rate_clean"] == 0.5
    assert o["precision"] == round(2 / 3, 3) and o["localization"] == round(2 / 3, 3) and o["category_accuracy"] == 0.5
    per = evaluate(results, labels, {}, {}, "source")["per_category"]
    assert per["omission"]["tp"] == 1 and per["omission"]["fn"] == 2 and per["omission"]["fp"] == 1


def test_no_labels_gives_empty_but_valid_report():
    c = evaluate([res(0)], {}, {}, {}, "source")
    assert c["labelled_items"] == 0 and c["overall"]["recall"] is None


def test_aliases():
    assert canon("Number or unit") == "number" and canon("number_or_unit") == "number"
    assert canon("Repetition") == "speech_repetition" and canon("garbled audio") == "garbled"
    assert canon("Something New") == "something_new"  # kept, not dropped


def test_collect_merges_file_over_inline(tmp_path):
    items = [{"id": "a", "lang": "hi", "labels": []}, {"id": "b", "lang": "hi", "labels": None}]
    f = tmp_path / "labels.jsonl"
    f.write_text("\n".join([json.dumps({"id": "a", "lang": "hi", "labels": [{"type": "Number", "span": [1, 2]}]}),
                            json.dumps({"id": "zz", "lang": "hi", "labels": []}), "not json"]), encoding="utf-8")
    labels, sources, warnings = collect(items, str(f))
    assert labels == {("a", "hi"): [{"type": "Number", "span": [1, 2], "category": "number"}]}
    assert sources == {"dataset_inline": 1, "calibration_file": 1} and len(warnings) == 1


def test_missing_calibration_file_warns():
    _, _, warnings = collect([], "nope.jsonl")
    assert warnings and "cannot read" in warnings[0]


def test_wilson():
    assert wilson(0, 0) is None
    lo, hi = wilson(8, 10)
    assert 0.44 < lo < 0.5 and 0.94 < hi < 0.97


def test_threshold_not_raised_past_most_labelled_errors():
    # The check fires on clean items as strongly as on errors: meeting the cap would silence it.
    results, labels = toy(MIN_POSITIVE, MIN_CLEAN, clean_strength=0.9, err_strength=0.9)
    t = fit_thresholds(results, labels)["back_translation:omission"]
    assert not t["moved"] and "would keep only 0 of 5" in t["reason"]


def test_speech_flag_without_text_span_matches_by_category():
    from bodhan_eval.calibrate import hits

    lab = {"category": "garbled", "span": [10, 20]}
    assert hits({"category": "garbled", "span": None, "source_span": None, "translation_span": None}, lab)
    assert not hits({"category": "omission", "span": None, "source_span": None, "translation_span": None}, lab)
    assert not hits({"category": "garbled", "span": [30, 40], "source_span": [30, 40], "translation_span": None}, lab)
