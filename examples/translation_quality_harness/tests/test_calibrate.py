import json

from bodhan_eval.calibrate import DEFAULT_THRESHOLDS, evaluate, evaluate_checks, explain_misses, human_review, tune_thresholds
from bodhan_eval.flags import Candidate, canonical_category, overlaps
from bodhan_eval.pipeline import load_dataset, merge, review_csv_judgments


def test_judge_explanations_never_carry_indic_text():
    from bodhan_eval.judge import english_only

    out = english_only('"The Constitution of India" is rendered as "ভারতের পুরস্কার" ("Prize of India").')
    assert out == '"The Constitution of India" is rendered as "‹…›" ("Prize of India").'
    assert not any("ঀ" <= c <= "૿" for c in english_only("हिन्दी और বাংলা text"))


def test_span_overlap_rules():
    assert overlaps((0, 5), (4, 9)) and not overlaps((0, 5), (5, 9))
    assert overlaps((5, 5), (0, 5))  # an insertion point touching a span counts
    assert not overlaps(None, (0, 1))


def test_label_aliases():
    assert canonical_category("Number or unit") == "number"
    assert canonical_category("Omission", area="speech") == "speech_omission"
    assert canonical_category("Wrong language or script") == "wrong_language"
    assert canonical_category("nonsense") is None


def test_merge_boosts_agreeing_checks_and_drops_judge_duplicates():
    cands = [
        Candidate("number", 0.95, "numbers", span=(10, 13)),
        Candidate("number", 0.8, "judge", span=(9, 14)),
        Candidate("mistranslation", 0.8, "judge", span=(10, 13)),
        Candidate("omission", 0.3, "length_ratio"),
    ]
    flags = merge(cands)
    num = next(f for f in flags if f["category"] == "number")
    assert num["score"] == 1.0 and num["checks"] == ["judge", "numbers"] and num["span"] == [10, 13]
    assert not any(f["category"] == "mistranslation" for f in flags)
    assert any(f["category"] == "omission" and f["span"] is None for f in flags)


def _cluster(cat, score, span=(0, 3)):
    return {"category": cat, "score": score, "span": list(span), "source_span": None}


def test_thresholds_are_tuned_from_labels():
    labelled = [([_cluster("number", 0.7)], [{"category": "number"}])] * 4 + [([_cluster("number", 0.5)], [])] * 4
    thr, notes = tune_thresholds(labelled)
    assert thr["number"] == 0.6 and notes["number"]["source"] == "tuned"  # midway between the negatives (0.5) and positives (0.7)
    assert thr["format"] == DEFAULT_THRESHOLDS["format"] and notes["format"]["source"] == "default"


def test_answered_review_queue_becomes_per_flag_judgments(tmp_path):
    path = tmp_path / "review_queue.csv"
    path.write_text(
        "id,lang,category,score,threshold,flagged,span,source_span,evidence,human_label (error|clean)\n"
        "a,hi,number,0.55,0.6,False,\"[3, 5]\",null,x,error\n"
        "b,ta,omission,0.65,0.6,True,null,\"[0, 4]\",x,clean\n"
        "c,bn,format,0.6,0.6,True,null,null,x,\n",
        encoding="utf-8",
    )
    assert review_csv_judgments(path) == [
        {"id": "a", "lang": "hi", "category": "number", "verdict": "error", "span": [3, 5], "annotator": "human:review-queue"},
        {"id": "b", "lang": "ta", "category": "omission", "verdict": "clean", "span": None, "annotator": "human:review-queue"},
    ]  # the unanswered row is ignored; a 'clean' verdict is about one category, not the whole item


def test_judgment_files_load_beside_item_labels(tmp_path):
    data = tmp_path / "d.jsonl"
    data.write_text(json.dumps({"id": "a", "source": "x", "lang": "hi", "translation": "y"}) + "\n", encoding="utf-8")
    labels, human = tmp_path / "l.jsonl", tmp_path / "h.jsonl"
    labels.write_text(json.dumps({"id": "a", "lang": "hi", "labels": [{"type": "omission", "span": [0, 1]}]}) + "\n", encoding="utf-8")
    human.write_text(
        json.dumps({"id": "a", "lang": "hi", "category": "Number or unit", "verdict": "clean", "annotator": "human:me"}) + "\n"
        + json.dumps({"id": "a", "lang": "hi", "category": "number", "verdict": "maybe"}) + "\n", encoding="utf-8")
    items, stats = load_dataset(data, ["hi"], [labels, human])
    assert [lab["category"] for lab in items[0]["labels"]] == ["omission"]  # item labels untouched by verdicts
    assert [(j["category"], j["verdict"], j["annotator"]) for j in stats["judgments"]] == [("number", "clean", "human:me")]
    assert len(stats["bad"]) == 1  # the malformed verdict is counted, not fatal


def test_human_review_scores_flags_against_a_persons_verdicts():
    judgments = [
        {"id": "a", "lang": "hi", "category": "number", "verdict": "error", "annotator": "human:me"},    # flagged -> true positive
        {"id": "b", "lang": "hi", "category": "number", "verdict": "clean", "annotator": "human:me"},    # flagged -> false alarm
        {"id": "c", "lang": "hi", "category": "number", "verdict": "error", "annotator": "human:me"},    # not flagged -> miss
        {"id": "d", "lang": "hi", "category": "number", "verdict": "clean", "annotator": "human:me"},    # not flagged -> cleared
        {"id": "zz", "lang": "hi", "category": "number", "verdict": "error", "annotator": "human:me"},   # not in this run
    ]
    clusters = {(i, "hi"): [_cluster("number", s)] for i, s in (("a", 0.9), ("b", 0.8), ("c", 0.3), ("d", 0.2))}
    clusters[("c", "hi")].append(_cluster("omission", 0.95))  # the missed number error was flagged as an omission instead
    hr = human_review(judgments, clusters, {"number": 0.6, "omission": 0.6})
    assert (hr["tp"], hr["fp"], hr["fn"], hr["tn"]) == (1, 1, 1, 1)
    assert hr["missed_but_flagged_as_other_category"] == 1
    assert hr["precision"] == 0.5 and hr["recall"] == 0.5 and hr["agreement"] == 0.5
    assert hr["skipped_not_in_run"] == 1 and hr["annotators"] == {"human:me": 5}


def test_metrics_match_assignment_definitions():
    rows = [
        {"lang": "hi", "flags": [_cluster("number", 1, (5, 8))], "labels": [{"category": "number", "span": (5, 8), "ref": "translation"}]},
        {"lang": "hi", "flags": [_cluster("mistranslation", 1, (0, 2))], "labels": [{"category": "term", "span": (10, 12), "ref": "translation"}]},
        {"lang": "ta", "flags": [], "labels": [{"category": "omission", "span": (0, 4), "ref": "source"}]},
        {"lang": "ta", "flags": [_cluster("format", 1)], "labels": []},
        {"lang": "ta", "flags": [], "labels": []},
    ]
    m = evaluate(rows)
    assert m["detection_recall"] == round(2 / 3, 3)
    assert m["false_alarm_rate"] == 0.5
    assert m["localization"] == 0.5
    assert m["category_accuracy"] == 0.5
    assert m["per_category"]["number"]["precision"] == 1.0
    # Per language, and the diagnostics that explain misses.
    assert m["per_language"]["hi"] == {"detection_recall": 1.0, "false_alarm_rate": None, "localization": 0.5,
                                       "category_accuracy": 0.5, "seeded_errors": 2, "clean_items": 0}
    assert m["per_language"]["ta"]["false_alarm_rate"] == 0.5
    d = m["diagnostics"]
    assert d["missed_by_category"] == {"omission": 1} and d["false_alarms_by_category"] == {"format": 1}
    assert d["category_confusions"] == {"term -> mistranslation": 1}
    notes = explain_misses(m, {"same_verdict_rate": 1.0})
    assert "omission" in notes["detection_recall"] and "format" in notes["false_alarm_rate"]


def test_each_check_is_scored_on_its_own():
    rows = [
        {"labels": [{"category": "number"}], "candidates": [{"check": "numbers", "category": "number"}, {"check": "judge", "category": "mistranslation"}]},
        {"labels": [], "candidates": [{"check": "numbers", "category": "number"}]},
        {"labels": [{"category": "number"}], "candidates": [{"check": "judge", "category": "number"}]},
    ]
    pc = evaluate_checks(rows)
    assert pc["numbers"]["number"] == {"tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5}
    assert pc["judge"]["number"]["recall"] == 0.5 and pc["judge"]["mistranslation"]["precision"] == 0.0
