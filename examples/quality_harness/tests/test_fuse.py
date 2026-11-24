from bodhan_eval.adjudicate import adjudicate
from bodhan_eval.calibrate import calibrate, evaluate, examples, fit, load_labels, rates
from bodhan_eval.fuse import fuse, load_thresholds

TH = {**load_thresholds(), "flag": 0.5, "uncertain": 0.3}  # fixed here: these tests are about the mechanics
SOURCE = "The fort was built in 1650. It has four gates."
TRANSLATION = "किला 1650 में बना था। इसमें चार द्वार हैं।"


def cand(check, category, span, side="translation", score=0.6, standalone=False, severity=None, text="", **evidence):
    return {"check": check, "category": category, "side": side, "span": list(span), "score": score,
            "standalone": standalone, "severity": severity, "evidence": evidence, "text": text}


def record(cands, id_="fort", lang="hi", translation=TRANSLATION):
    return {"id": id_, "lang": lang, "source": SOURCE, "translation": translation, "checks": {}, "candidates": cands,
            "translation_origin": "bodhan"}


def test_agreement_at_one_place_is_one_flag_and_one_family_counts_once():
    bt = cand("back_translation", "mistranslation", (0, 20))
    phrase = cand("back_translation_phrase", "mistranslation", (5, 12))
    (alone,), verdict, _ = fuse(record([bt, phrase]), TH)
    assert alone["confidence"] == 0.35 and verdict == "uncertain"  # two back-translation checks: max, not both
    judge = cand("judge", "omission", (2, 8), score=0.8, severity="minor")
    (flag,), verdict, mqm = fuse(record([bt, phrase, judge]), TH)
    assert flag["confidence"] == round(1 - 0.65 * 0.4, 3) and flag["decision"] == "flag" and verdict == "flagged"
    assert flag["category"] == "omission" and flag["alt_categories"] == ["mistranslation"]
    assert flag["severity"] == "minor" and mqm == 1 and flag["candidates"] == [0, 1, 2]


def test_separate_places_and_item_level_checks_stay_apart():
    flags, verdict, mqm = fuse(record([
        cand("numbers", "number", (5, 9), standalone=True, severity="major", score=1.0),
        cand("judge", "term", (22, 40), score=0.6),
        cand("length", "omission", (0, len(TRANSLATION))),
    ]), TH)
    assert [(f["category"], f["decision"]) for f in flags] == [("number", "flag"), ("term", "uncertain"), ("omission", "below")]
    assert verdict == "flagged" and mqm == 5
    assert fuse({"checks": {"translation": "skipped: no translation"}}, TH) == ([], "skipped", 0)


def test_other_side_is_matched_by_relative_position():
    src = cand("judge", "omission", (28, 47), side="source", score=0.8)  # "It has four gates."
    (f,), _, _ = fuse(record([src, cand("entities", "term", (24, 41))]), TH)  # second half of the translation
    assert f["sources"] == ["entities", "judge"]


def test_calibration_scores_variants_against_their_base_and_counts_clean_false_alarms(tmp_path):
    noise = cand("judge", "term", (0, 4), score=0.8)  # on the base too: neither right nor wrong on the variant
    base = record([noise])
    number = cand("numbers", "number", (5, 9), standalone=True, severity="major", score=1.0)
    variant = record([noise, number], id_="fort~number", translation=TRANSLATION.replace("1650", "1655"))
    clean = record([cand("back_translation_phrase", "omission", (0, 4))], id_="other")
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        '{"id": "fort~number", "lang": "hi", "base": "fort", "labels": [{"type": "number", "side": "translation", "span": [5, 9]}]}\n'
        '{"id": "other", "lang": "hi", "labels": []}\n'
        '{"id": "fort", "lang": "hi"}\n', encoding="utf-8")
    lab = load_labels([labels])
    exs, missing = examples([base, variant, clean], lab)
    assert missing == 0 and [len(e["cands"]) for e in exs] == [1, 1]  # fort~number: only the new one; other: all
    a = rates(evaluate(exs, TH))["all"]["all"]
    assert (a["recall"], a["precision"], a["clean_items"], a["false_alarm_rate"]) == (1.0, 1.0, 1, 0.0)

    fitted, counts = fit(exs, TH)
    assert counts["numbers:strong|number|all"] == [1, 1]
    assert TH["weights"]["numbers:strong"]["*"] < fitted["weights"]["numbers:strong"]["*"] < 1  # shrunk, not 100%

    report, tuned = calibrate([base, variant, clean], lab, TH)
    assert report["labelled_items"]["clean"] == 1 and 0.2 <= tuned["flag"] <= 0.95


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.messages = reply, []

    def llm(self, messages, repeat=0):
        self.messages.append(messages)
        return self.reply


def test_adjudicator_answers_decide_uncertain_flags_and_are_reused_on_refusing():
    rec = record([cand("judge", "mistranslation", (0, 4), score=0.8, explanation="says fort, means palace"),
                  cand("judge", "term", (22, 40), score=0.6, explanation="vaguer word")])
    th = {**TH, "flag": 0.65, "uncertain": 0.45, "adjudicate": True}
    flags, verdict, _ = fuse(rec, th)
    assert [f["decision"] for f in flags] == ["uncertain", "uncertain"] and verdict == "uncertain"
    fake = FakeLLM({"claims": [
        {"claim": "C1", "is_error": "yes", "category": "term_entity", "severity": "minor", "reason": "किला is right but 1650"},
        {"claim": "C2", "is_error": "no", "reason": "acceptable synonym"},
        {"claim": "C9", "is_error": "yes"},  # no such claim: ignored
    ]})
    rec["adjudication"] = adjudicate(rec, flags, fake)
    user = fake.messages[0][1]["content"]
    assert "C1: mistranslation" in user and "says fort, means palace" in user and "C2: term" in user
    flags, verdict, mqm = fuse(rec, th)
    assert [(f["category"], f["decision"], f["severity"]) for f in flags] == [("term", "flag", "minor"), ("term", "below", "major")]
    assert flags[0]["adjudicator"]["reason"] == "किला is right but 1650" and verdict == "flagged" and mqm == 1
    assert [f["decision"] for f in fuse(rec, {**th, "adjudicate": False})[0]] == ["uncertain", "uncertain"]


def test_label_names_from_other_sources_map_onto_ours():
    from bodhan_eval.categories import label_categories

    assert label_categories({"type": "Number or unit"}) == ("number", {"number"})
    assert label_categories({"type": "term_entity"}) == ("term", {"term"})
    assert label_categories({"type": "Garbled audio"}) == ("speech_garbled", {"speech_garbled"})
    assert label_categories({"type": "truncation"}) == ("speech_omission", {"speech_omission"})
    assert label_categories({"type": "omission", "time": [1.0, 2.0]}) == ("speech_omission", {"speech_omission"})
    assert label_categories({"type": "omission"}) == ("omission", {"omission", "speech_omission"})  # can't tell
    assert label_categories({"type": "Wrong language or script"})[0] == "wrong_language"


def test_a_label_with_a_span_but_no_side_matches_either_text(tmp_path):
    labels = tmp_path / "l.jsonl"
    labels.write_text('{"id": "fort", "lang": "hi", "labels": [{"type": "number_unit", "span": [22, 26]}]}\n', encoding="utf-8")
    (ex,), _ = examples([record([cand("numbers", "number", (22, 26), side="source", standalone=True, score=1.0)])],
                        load_labels([labels]))
    a = rates(evaluate([ex], TH))["all"]["all"]
    assert a["recall"] == 1.0 and a["recall_right_category"] == 1.0
