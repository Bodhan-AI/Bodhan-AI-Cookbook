"""test_judge_fusion: Block 6: judge parsing, repair retry, skip rule, fusion rules, consistency. LLM mocked.
test_calibration: Block 6: Wilson intervals, label loading, split, tuning on train only, metrics, end to end.
"""

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from bodhan_eval import calibration as cal
from bodhan_eval.calibration import calibrate, evaluate, load_labels, split_of
from bodhan_eval.checks import Flag
from bodhan_eval.config import DEFAULTS
from bodhan_eval.dataset import _parse_label
from bodhan_eval.infra import wilson
from bodhan_eval.judge import REPAIR, SYSTEM, build_user, fuse, judge_item, parse_judgment, rerun_consistency, should_skip, strong_flags

# ==================== judge_fusion
SRC = "So, it is likely that the notation was added simply as a label."


TGT = "इसलिए, यह संभावना है कि संकेतन को केवल एक लेबल के रूप में जोड़ा गया था।"


QUOTE = TGT[TGT.index("केवल"):TGT.index("केवल") + len("केवल एक लेबल")]


INDIC = re.compile(r"[ऀ-෿]")


TH = dict(DEFAULTS)


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """complete_json returns the queued answers in order (dict, or a raw string)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def complete_json(self, system, user, *, variant="", bypass_cache=False, max_tokens=2000):
        self.calls.append({"system": system, "user": user, "variant": variant, "bypass": bypass_cache})
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        raw = a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)
        data = a if isinstance(a, dict) else None
        return SimpleNamespace(data=data, raw=raw, cached=False, prompt_tokens=10, completion_tokens=5)


def err(category="mistranslation", quote=QUOTE, confidence=0.8, **kw):
    return {"category": category, "severity": "major", "quote": quote, "explanation": "Wrong word.", "confidence": confidence, **kw}


def flag(check_id, category, *, weak=False, span=(0, 5), unit="chars"):
    return Flag(check_id, category, "major", span, unit, "m", {}, 0.5, needs_corroboration=weak).to_dict()


def test_prompt_is_english_only():
    assert not INDIC.search(SYSTEM) and not INDIC.search(REPAIR)
    assert "Never write, rewrite, translate or correct" in SYSTEM
    user = build_user(SRC, TGT, "hi", ["number_unit: hint"])
    assert TGT in user and not INDIC.search(user.replace(TGT, ""))  # the only Indic text is the item's own
    judge_dir = Path(__file__).resolve().parents[1] / "src" / "bodhan_eval" / "judge"
    for f in judge_dir.glob("*.py"):
        assert not INDIC.search(f.read_text(encoding="utf-8")), f.name


def test_parse_quote_found_maps_to_original_span():
    verdict, errors, dropped = parse_judgment({"verdict": "errors", "errors": [err()]}, TGT)
    assert verdict == "errors" and not dropped
    e = errors[0]
    assert e["quote_found"] and TGT[e["span"][0]:e["span"][1]] == QUOTE
    assert e["confidence"] == 0.8


def test_parse_quote_not_found_lowers_confidence():
    _, errors, _ = parse_judgment({"verdict": "errors", "errors": [err(quote="a paraphrase")]}, TGT)
    assert errors[0]["span"] is None and not errors[0]["quote_found"]
    assert errors[0]["confidence"] == pytest.approx(0.4)


def test_parse_drops_speech_and_unknown_categories_and_trims_explanation():
    long = " ".join(["word"] * 40)
    data = {"verdict": "errors", "errors": [err(category="speech_garbled"), err(category="style"), err(category="Wrong-Meaning", explanation=long), "junk"]}
    _, errors, dropped = parse_judgment(data, TGT)
    assert [e["category"] for e in errors] == ["mistranslation"]  # alias mapped
    assert len(errors[0]["explanation"].split()) == 25
    assert sorted(dropped) == ["category:speech_garbled", "category:unmapped:style", "not_an_object"]


def test_parse_ok_verdict_wins():
    verdict, errors, dropped = parse_judgment({"verdict": "ok", "errors": [err()]}, TGT)
    assert verdict == "ok" and errors == [] and dropped


@pytest.mark.parametrize("bad", [None, {"verdict": "maybe"}, {"verdict": "errors", "errors": "x"}])
def test_parse_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_judgment(bad, TGT)


def test_judge_repairs_once():
    llm = FakeLLM(["not json at all", {"verdict": "errors", "errors": [err()]}])
    out = run(judge_item(llm, source=SRC, translation=TGT, lang="hi", hints=[]))
    assert out.status == "ran" and out.repaired and len(out.errors) == 1
    assert len(llm.calls) == 2 and REPAIR in llm.calls[1]["user"]


def test_judge_gives_up_after_one_repair():
    llm = FakeLLM(["nope", "still nope"])
    out = run(judge_item(llm, source=SRC, translation=TGT, lang="hi", hints=[]))
    assert out.status == "error" and "invalid_json_after_repair" in out.reason
    assert len(llm.calls) == 2


def test_skip_rule():
    assert should_skip([flag("numbers", "number_unit")]) == "tier1_unambiguous:numbers"
    assert should_skip([flag("length_ratio", "omission", weak=True)]) is None
    assert should_skip([flag("audio_silence", "speech_garbled", unit="seconds", span=(0.0, 1.0))]) is None


def test_fusion_judge_plus_corroboration_is_a_flag():
    e = parse_judgment({"verdict": "errors", "errors": [err(category="omission")]}, TGT)[1]
    out = fuse([flag("length_ratio", "omission", weak=True)], e, TH)
    assert len(out) == 1 and not out[0]["needs_corroboration"]
    assert out[0]["evidence"]["corroborated_by"] == ["length_ratio"]


def test_fusion_judge_alone_goes_to_review_queue():
    e = parse_judgment({"verdict": "errors", "errors": [err()]}, TGT)[1]
    out = fuse([flag("format", "format")], e, TH)  # incompatible category does not corroborate
    assert out[0]["needs_corroboration"] and out[0]["evidence"]["review_queue"] == "judge_only"


def test_fusion_quote_not_found_goes_to_review_even_if_corroborated():
    e = parse_judgment({"verdict": "errors", "errors": [err(category="addition", quote="xyz", confidence=1.0)]}, TGT)[1]
    out = fuse([flag("back_translation", "addition", weak=True)], e, TH)
    assert out[0]["needs_corroboration"] and out[0]["evidence"]["review_queue"] == "quote_not_found"
    assert out[0]["span"] is None


def test_fusion_low_confidence_dropped_and_per_category_cutoff():
    e = parse_judgment({"verdict": "errors", "errors": [err(confidence=0.3)]}, TGT)[1]
    corr = [flag("back_translation", "addition", weak=True)]
    assert fuse(corr, e, TH) == []
    assert len(fuse(corr, e, TH | {"judge_min_confidence.mistranslation": 0.2})) == 1


def test_roundtrip_drift_alone_is_never_a_flag():
    item = {"flags": [flag("back_translation", "omission", weak=True), flag("asr_roundtrip", "speech_garbled", weak=True, unit="seconds", span=(0.0, 1.0))],
            "judge": {"status": "skipped"}}
    assert strong_flags(item, TH) == []


def test_consistency_counts_same_verdict_per_language():
    first_ok = SimpleNamespace(verdict="ok", errors=[])
    first_err = SimpleNamespace(verdict="errors", errors=[{"category": "omission"}])
    jobs = [{"id": "x1", "lang": "hi", "source": SRC, "translation": TGT, "hints": [], "outcome": first_ok},
            {"id": "x2", "lang": "hi", "source": SRC, "translation": TGT, "hints": [], "outcome": first_err}]
    llm = FakeLLM([{"verdict": "ok", "errors": []}])  # second run says ok both times
    res = run(rerun_consistency(llm, jobs, ["hi", "ta"]))
    assert all(c["variant"] == "consistency-1" and not c["bypass"] for c in llm.calls)
    assert res["by_lang"]["hi"]["same_verdict"]["k"] == 1 and res["by_lang"]["hi"]["same_verdict"]["n"] == 2
    assert res["by_lang"]["ta"]["same_verdict"]["value"] is None  # language without items


# ==================== calibration
TH = dict(DEFAULTS)


def test_wilson_matches_brief():
    lo, hi = wilson(40, 50)
    assert (round(lo, 3), round(hi, 3)) == (0.670, 0.888)
    assert round(wilson(5, 50)[0], 3) == 0.043 and round(wilson(5, 50)[1], 3) == 0.214
    assert wilson(0, 50)[0] == 0.0 and round(wilson(0, 50)[1], 3) == 0.071
    assert wilson(0, 0) == (0.0, 1.0)


def test_load_labels_aliases_unmapped_and_bad_rows(tmp_path):
    p = tmp_path / "labels.jsonl"
    rows = [
        {"id": "a", "labels": [{"type": "Missing", "span": [0, 3], "source": "injector"}]},
        {"id": "b", "labels": [{"type": "weird-thing"}]},
        {"id": "c", "labels": []},
        {"id": "d"},
        {"labels": []},
        "not json",
    ]
    p.write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in rows) + "\n", encoding="utf-8")
    labels, info = load_labels(p)
    assert labels["a"][0].category == "omission"
    assert labels["b"][0].category == "unmapped:weird_thing" and info["unmapped"] == {"unmapped:weird_thing": 1}
    assert labels["c"] == [] and "d" not in labels
    assert info["bad_rows"] == 2 and info["rows_without_labels_field"] == 1
    assert info["by_source"] == {"injector": 1, "unspecified": 1}


def test_split_is_stable_and_mixed():
    ids = [f"i{n:03d}" for n in range(200)]
    first = [split_of(i) for i in ids]
    assert first == [split_of(i) for i in ids]
    share = first.count("train") / len(ids)
    assert 0.6 < share < 0.8


def jflag(category, span=(0, 4), weak=False):
    return Flag("numbers" if category == "number_unit" else "length_ratio", category, "major", span, "chars", "m", {}, 0.5,
                needs_corroboration=weak).to_dict()


def item(i, lang="hi", flags=(), errors=(), verdict=None):
    j = {"status": "ran", "verdict": verdict or ("errors" if errors else "ok"), "errors": list(errors)}
    return {"id": i, "lang": lang, "status": "ok", "flags": list(flags), "judge": j}


def jerr(category="omission", conf=0.8, span=(0, 4)):
    return {"category": category, "severity": "major", "quote": "q", "explanation": "e", "confidence": conf,
            "span": list(span), "quote_found": True, "match": "exact"}


def lab(category="omission", span=(0, 4), source="injector"):
    return _parse_label({"type": category, "span": list(span), "source": source})


def test_metrics_definitions_strict_vs_reviewed_and_empty_language():
    items = [
        item("e1", flags=[jflag("number_unit", span=(10, 12))]),  # error item, flag elsewhere -> detected, not localized
        item("e2", flags=[jflag("omission", span=(2, 6))]),  # detected + localized + right category
        item("c1"),  # clean, not flagged
        item("p1", flags=[jflag("format")]),  # only a pre-existing defect: flagged
    ]
    labels = {"e1": [lab("omission")], "e2": [lab("omission")], "c1": [], "p1": [lab("format", source="reviewed_preexisting")]}
    m = evaluate(items, labels, TH, ["hi", "ta"])["overall"]
    assert m["recall_items"]["k"] == 3 and m["recall_items"]["n"] == 3  # p1 has a label, so it is an error item
    assert m["localization"]["k"] == 2 and m["localization"]["n"] == 3
    assert m["category_accuracy"]["k"] == 2
    assert (m["false_alarm_strict"]["k"], m["false_alarm_strict"]["n"]) == (1, 2)  # p1 counts as clean in strict
    assert (m["false_alarm_reviewed"]["k"], m["false_alarm_reviewed"]["n"]) == (0, 1)
    by_lang = evaluate(items, labels, TH, ["hi", "ta"])["by_lang"]
    assert by_lang["ta"]["items"] == 0 and by_lang["ta"]["recall_items"]["value"] is None


def _ids(split, n, prefix):
    out, k = [], 0
    while len(out) < n:
        i = f"{prefix}{k}"
        if split_of(i) == split:
            out.append(i)
        k += 1
    return out


def _dataset(n_err, n_clean, prefix, err_conf, clean_conf, split):
    """Error items: judge omission error (corroborated by a weak length flag) at err_conf.
    Clean items: the same pattern at clean_conf (a false alarm if it passes the cutoff)."""
    items, labels = [], {}
    for i in _ids(split, n_err, prefix + "e"):
        items.append(item(i, flags=[jflag("omission", weak=True)], errors=[jerr(conf=err_conf)]))
        labels[i] = [lab()]
    for i in _ids(split, n_clean, prefix + "c"):
        items.append(item(i, flags=[jflag("omission", weak=True)], errors=[jerr(conf=clean_conf)]))
        labels[i] = []
    return items, labels


def test_tuning_uses_train_only_and_reports_heldout():
    tr_items, tr_labels = _dataset(12, 12, "t", err_conf=0.85, clean_conf=0.45, split="train")
    ho_items, ho_labels = _dataset(4, 4, "h", err_conf=0.85, clean_conf=0.45, split="heldout")
    th, th_json, report = calibrate(tr_items + ho_items, tr_labels | ho_labels, {}, TH, ["hi"])
    assert 0.45 < th["judge_min_confidence"] <= 0.85  # cut between the clean and error confidences
    assert report["split"] == {"train": 24, "heldout": 8}
    assert report["heldout"]["overall"]["items"] == 8
    assert th_json["derived_from"]["train_items"] == 24
    # changing held-out items must not change the chosen threshold
    ho2, _ = _dataset(4, 4, "h", err_conf=0.15, clean_conf=0.95, split="heldout")
    th2, _, _ = calibrate(tr_items + ho2, tr_labels | ho_labels, {}, TH, ["hi"])
    assert th2["judge_min_confidence"] == th["judge_min_confidence"]


def test_per_category_only_with_enough_positives():
    few, few_l = _dataset(cal.MIN_CATEGORY_POSITIVES - 1, 5, "f", 0.85, 0.45, "train")
    _, js, _ = calibrate(few, few_l, {}, TH, ["hi"])
    assert js["derived_from"]["per_category"] == {}
    many, many_l = _dataset(cal.MIN_CATEGORY_POSITIVES, 5, "m", 0.85, 0.45, "train")
    _, js, _ = calibrate(many, many_l, {}, TH, ["hi"])
    assert "omission" in js["derived_from"]["per_category"]


def test_calibrate_without_judge_outputs_keeps_defaults():
    items = [item("x1", flags=[jflag("number_unit")])]
    th, js, report = calibrate(items, {"x1": [lab("number_unit")]}, {}, TH, ["hi", "bn"])
    assert th["judge_min_confidence"] == DEFAULTS["judge_min_confidence"]
    assert js["derived_from"]["status"].startswith("defaults")
    assert report["status"] == "ok"


class FakeLLMClient:
    def __init__(self, *a, **k):
        pass

    async def complete_json(self, system, user, *, variant="", bypass_cache=False, max_tokens=2000):
        from types import SimpleNamespace
        data = {"verdict": "errors", "errors": [{"category": "number_unit", "severity": "major", "quote": "100", "explanation": "x", "confidence": 0.9}]}
        return SimpleNamespace(data=data, raw=json.dumps(data), cached=False, prompt_tokens=10, completion_tokens=5)

    async def aclose(self):
        pass


def test_pipeline_with_mocked_judge_and_calibration(tmp_path, monkeypatch):
    from bodhan_eval import pipeline
    from bodhan_eval.pipeline import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setattr(pipeline, "LLMClient", FakeLLMClient)
    rows = [  # translations are the harness's own test strings (digits only differ)
        {"id": f"r{n}", "source": "Water boils at 100 °C.", "lang": "hi", "translation": "पानी 100 °C पर उबलता है।", "labels": []}
        for n in range(10)
    ]
    ds = tmp_path / "d.jsonl"
    ds.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    outs = []
    for k in range(2):
        out = tmp_path / f"o{k}"
        assert main(["--dataset", str(ds), "--langs", "hi,ta", "--out", str(out), "--cache-dir", str(tmp_path / "c"),
                     "--calibration", str(ds), "--judge-max-items", "3"]) == 0
        outs.append(out)
    items = [json.loads(x) for x in (outs[0] / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    statuses = sorted(i["judge"]["status"] + ":" + i["judge"].get("reason", "") for i in items)
    assert statuses.count("ran:") == 3 and statuses.count("skipped:judge_max_items") == 7
    judged = [i for i in items if i["judge"]["status"] == "ran"]
    assert all(i["review_queue"] for i in judged)  # judge alone (no corroboration) -> review queue only
    assert not [f for i in items for f in i["flags"] if f["check_id"] == "llm_judge" and not f["needs_corroboration"]]
    cal_json = json.loads((outs[0] / "calibration.json").read_text(encoding="utf-8"))
    assert cal_json["status"] == "ok" and cal_json["heldout"]["by_lang"]["ta"]["items"] == 0
    assert (outs[0] / "thresholds.json").exists()
    for name in ("summary.md", "summary.json", "items.jsonl", "calibration.json"):
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes(), name
